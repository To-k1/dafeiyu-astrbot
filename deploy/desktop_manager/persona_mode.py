"""Select the paired FishHome personas without rewriting existing memories."""

import asyncio
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from plugin.astrbot_plugin_bili_activity.qq_conversations import restore_persona_chat
from scripts.native_archive import snapshot


TEST_PREFIX = "fish-home-"
FORMAL_PREFIX = "feiyu-"
ATTRIBUTES = ("system_prompt", "begin_dialogs", "tools", "skills", "custom_error_message")


def settings(astro):
    path = astro / "data/cmd_config.json"
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    runner = cfg["agent_runner"]
    target = runner["config"]["persona"] if runner["runner_type"] == "local" else runner["config"]
    return path, cfg, target


def paired(persona):
    if not isinstance(persona, str):
        return None
    match = re.fullmatch(r"(fish-home-|feiyu-)(v[0-9]+(?:\.[0-9]+)*)", persona)
    if match is None:
        return None
    prefix, version = match.groups()
    return {"mode": "test" if prefix == TEST_PREFIX else "formal",
            "current": persona, "test": TEST_PREFIX + version, "formal": FORMAL_PREFIX + version}


def status(astro):
    try:
        _, _, target = settings(astro)
        current = target.get("persona_id")
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        return {"mode": "unavailable"}
    return paired(current) or {"mode": "unavailable", "current": current}


def _write_config(path, content):
    from .windows import write_json
    write_json(path, content)


def backup(astro, state):
    directory = state / datetime.now().strftime("before-persona-mode-%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    cfg = astro / "data/cmd_config.json"
    (directory / cfg.name).write_bytes(cfg.read_bytes())
    core = astro / "data/data_v4.db"
    with closing(sqlite3.connect(core.as_uri() + "?mode=ro", uri=True)) as source, \
            closing(sqlite3.connect(directory / core.name)) as target:
        source.backup(target)
    snapshot(astro, directory / "runtime.zip", environment="prod", revision=None, writers_stopped=True)
    return directory


def read_owner_binding(astro, scope):
    path = astro / "data/plugin_data/astrbot_plugin_bili_activity/control.json"
    control = json.loads(path.read_text(encoding="utf-8-sig"))
    if control.get("owner_scope") != scope or not re.fullmatch(r"[^:]+:FriendMessage:\d+", scope):
        raise ValueError("主人私聊配置不一致")


async def refresh_test_persona(db, pair, official, testing):
    fields = {key: getattr(official, key) for key in ATTRIBUTES}
    if testing is None:
        return await db.insert_persona(pair["test"], **fields, folder_id=official.folder_id)
    if any(getattr(testing, key) != val for key, val in fields.items()):
        return await db.update_persona(pair["test"], **fields)
    return testing


def require_idle(astro, state, *, operation="切换人格"):
    data = astro / "data"
    ledger = data / "plugin_data/astrbot_plugin_bili_activity/activity.sqlite3"
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as db:
        busy = db.execute("""SELECT 1 FROM settings WHERE
            ((key LIKE 'dsh-chat:v1:%' OR key LIKE 'dsh-owner-chat:v1:%' OR key LIKE 'dsh-meeting:v1:%')
             AND json_extract(value,'$.status') IN ('queued','running','stopping'))
            OR (key LIKE 'commissioned-file:v1:%'
                AND json_extract(value,'$.state') IN ('submitting','submitted')) LIMIT 1""").fetchone()
        if busy or db.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
            raise ValueError(f"有生活活动或小队任务在执行，请在任务结束后{operation}")
    outbox = data / "fishhome-qq-outbox.sqlite3"
    if outbox.is_file():
        with closing(sqlite3.connect(outbox.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT 1 FROM messages WHERE state='sending' LIMIT 1").fetchone():
                raise ValueError("QQ 正在投递消息，请稍候切换")
    for path in (state / "dsh-public-jobs").glob("*.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("state") in {"queued", "running", "cancelling"}:
            raise ValueError(f"有网页或视频任务在执行，请在任务结束后{operation}")


async def activate(astro, mode, scope):
    os.environ["ASTRBOT_ROOT"] = str(astro)
    from astrbot.core import db_helper, sp
    from astrbot.core.conversation_mgr import ConversationManager

    path, cfg, target = settings(astro)
    pair = paired(target.get("persona_id"))
    if pair is None or mode not in {"formal", "test"}:
        raise ValueError("全局人格需要使用 FishHome 的测试或正式版本 ID")
    desired = pair[mode]
    if desired == pair["current"]:
        return
    read_owner_binding(astro, scope)
    try:
        service = await sp.get_async(scope="umo", scope_id=scope, key="session_service_config", default={})
        if service.get("persona_id"):
            raise ValueError("主人私聊固定了单独人格，请先移除该覆盖")
        testing = await db_helper.get_persona_by_id(pair["test"])
        official = await db_helper.get_persona_by_id(pair["formal"])
        if official is None:
            if testing is None:
                raise ValueError("未找到同版本的正式或测试人格")
            official = await db_helper.insert_persona(pair["formal"], **{
                key: getattr(testing, key) for key in ATTRIBUTES}, folder_id=testing.folder_id)
        if mode == "test":
            testing = await refresh_test_persona(db_helper, pair, official, testing)
            if testing is None or any(getattr(testing, key) != getattr(official, key) for key in ATTRIBUTES):
                raise ValueError("测试人格配置未能从正式版同步")
            refresh_team(astro, scope)
        manager = ConversationManager(db_helper)
        previous = await restore_persona_chat(manager, sp, scope, pair["current"], desired)
        try:
            target["persona_id"] = desired
            _write_config(path, cfg)
        except BaseException:
            if previous:
                await manager.switch_conversation(scope, previous)
            else:
                await sp.session_remove(scope, "sel_conv_id")
            raise
    finally:
        await db_helper.engine.dispose()


def roster_store(astro, scope):
    read_owner_binding(astro, scope)
    activity = astro / "data/plugin_data/astrbot_plugin_bili_activity"
    sys.path.insert(0, str(astro))
    from data.plugins.astrbot_plugin_bili_activity.core import Store
    from data.plugins.astrbot_plugin_bili_activity.dsh_roster import DshRoster

    store = Store(activity)
    return store, DshRoster(store, scope)


def refresh_team(astro, scope):
    store, roster = roster_store(astro, scope)
    with closing(store.db):
        return roster.refresh_test_profiles()


def archive_test_agents(astro, scope):
    """Archive the existing team once at the initial transition to formal."""
    store, roster = roster_store(astro, scope)
    try:
        agents = [row for row in roster.list() if row["status"] != "archived"]
        for row in agents:
            roster.update(row["id"], status="archived")
        return len(agents)
    finally:
        store.db.close()


if __name__ == "__main__":
    astro, state, mode, scope = sys.argv[1:5]
    astro, state = Path(astro), Path(state)
    print("backup", backup(astro, state).name, flush=True)
    asyncio.run(activate(astro, mode, scope))
    if sys.argv[5:] == ["--archive-existing"]:
        print("archived", archive_test_agents(astro, scope), flush=True)
