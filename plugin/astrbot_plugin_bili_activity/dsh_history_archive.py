"""Export one named agent's FishHome records without touching execution queues."""

import json
import logging
import sqlite3
from contextlib import closing
from io import BytesIO
from time import time
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from .dsh_cards import export_card, import_card_files, unpack
from .dsh_roster import AGENT_ID, COPY_MEMORY_PREFIX, DshRoster, copy_memory_key
from .team_history import COPY_PREFIX, TeamHistory
from .team_memory_backend import MemoryUncertainError, TeamMemoryBackend


HISTORY_FORMAT = "fishhome-dsh-history/v1"
MAX_ARCHIVE = 32 * 1024 * 1024
logger = logging.getLogger(__name__)


def read_archive(body):
    if not body or len(body) > MAX_ARCHIVE:
        raise ValueError("经历档案为空或超过 32 MiB")
    required = {"archive.json", "identity.json", "history.json", "memories.json", "role.zip"}
    try:
        with ZipFile(BytesIO(body)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (len(names) != len(entries) or not required <= names <= required | {"README.md"}
                    or sum(entry.file_size for entry in entries) > MAX_ARCHIVE):
                raise ValueError("经历档案的文件清单或大小无效")
            card = unpack(archive.read("role.zip"), max_size=MAX_ARCHIVE)
            manifest, identity, history, memories = (
                json.loads(archive.read(name)) for name in ("archive.json", "identity.json", "history.json", "memories.json"))
    except (BadZipFile, UnicodeError, json.JSONDecodeError, NotImplementedError, RuntimeError, RecursionError) as exc:
        raise ValueError("无法读取经历档案 ZIP") from exc
    if (not isinstance(manifest, dict) or manifest.get("format") != HISTORY_FORMAT
            or not isinstance(identity, dict) or not isinstance(identity.get("id"), str)
            or not AGENT_ID.fullmatch(identity["id"]) or identity["id"] != manifest.get("agent_id")
            or not isinstance(history, list) or not isinstance(memories, list)
            or manifest.get("records") != len(history) or manifest.get("memories") != len(memories)):
        raise ValueError("经历档案清单和内容不一致")
    profiles = identity.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("经历档案没有人格版本")
    for number, profile in enumerate(profiles, 1):
        if not isinstance(profile, dict) or profile.get("version") != number:
            raise ValueError("人格版本记录无效")
        DshRoster.validate_profile(profile.get("name"), profile.get("persona"))
    if (profiles[-1]["name"] != card["name"] or profiles[-1]["persona"] != card["persona"]):
        raise ValueError("角色卡与最后一版人格不一致")
    original = identity["id"]
    refs = set()
    for entry in history:
        try:
            belongs = original in TeamHistory._participants(entry["kind"], entry["source"])
        except (TypeError, AttributeError, KeyError):
            belongs = False
        if (not isinstance(entry, dict) or not isinstance(entry.get("ref"), str)
                or not 1 <= len(entry["ref"]) <= 200 or entry["ref"] in refs
                or entry.get("kind") not in {"chat", "meeting", "task", "public", "agent", "missing"}
                or not isinstance(entry.get("source"), dict)
                or not belongs):
            raise ValueError("档案含有无效的经历引用或归属")
        refs.add(entry["ref"])
    memory_ids = set()
    for item in memories:
        if (not isinstance(item, dict) or type(item.get("original_id")) is not int
                or item["original_id"] in memory_ids or not isinstance(item.get("text"), str)
                or not item["text"].strip() or not isinstance(item.get("metadata"), dict)
                or item["metadata"].get("fishhome_owner") != original):
            raise ValueError("档案含有无效记忆或记忆归属不一致")
        memory_ids.add(item["original_id"])
    return card, identity, history, memories


def copied_sessions(identity):
    sessions = []
    mapping = {}
    prior_sessions = identity.get("chat_sessions") or []
    if not isinstance(prior_sessions, list) or not isinstance(identity.get("current_chat_session"), (str, type(None))):
        raise ValueError("历史日常会话记录无效")
    for item in prior_sessions:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not AGENT_ID.fullmatch(item["id"]) or item["id"] in mapping
                or item.get("status") not in {"active", "archived"}
                or not isinstance(item.get("title"), str) or len(item["title"]) > 80):
            raise ValueError("历史日常会话记录无效")
        previous = item["id"]
        mapping[previous] = uuid4().hex[:16]
        ancestry = item.get("copied_from")
        sessions.append({"id": mapping[previous], "title": item["title"], "status": item["status"],
                         "created": item.get("created", time()), "persona_version": item.get("persona_version"),
                         "native_session": None, "copied_from": previous,
                         **({"prior_session": ancestry} if isinstance(ancestry, str) else {})})
    return sessions, mapping


def import_status(store, scope, agent_id):
    row = store.get_setting(copy_memory_key(scope, agent_id))
    if row is None:
        return {"state": "ready"}
    return {"state": row["state"], "copied": row["next"], "total": row["total"],
            **({"error": row["error"]} if row.get("error") else {})}


def copy_from_archive(store, scope, archive, requested_name=""):
    card, identity, old_history, memories = archive
    history = list(old_history)
    roster = DshRoster(store, scope)
    sessions, session_ids = copied_sessions(identity)
    with store.transaction():
        name = requested_name.strip() or card["name"]
        row = roster.create(name, card["persona"])
        ident = row["id"]
        now = time()
        profiles = [{key: profile.get(key) for key in ("version", "name", "persona", "at")}
                    for profile in identity["profiles"]]
        profiles[-1]["name"] = name
        current = session_ids.get(identity.get("current_chat_session"), sessions[0]["id"] if sessions else None)
        if not sessions:
            root = uuid4().hex[:16]
            sessions.append({"id": root, "title": "日常对话", "status": "active", "native_session": None,
                             "created": now, "persona_version": profiles[-1]["version"], "copied_from": ""})
            current = root
        row.update(created=now, updated=now, profiles=profiles, version=profiles[-1]["version"],
                   status="paused" if memories else "active", chat_sessions=sessions, current_chat_session=current)
        legacy = identity.get("legacy_chat_session_id")
        if isinstance(legacy, str) and legacy in session_ids:
            row["legacy_chat_session_id"] = session_ids[legacy]
        roster.save(row)
        for profile in identity["profiles"]:
            history.append({"ref": f"agent:{identity['id']}:{profile['version']}", "kind": "agent",
                            "source": {"id": identity["id"], "profile": profile}})
        history_key = f"{COPY_PREFIX}{scope}:{ident}"
        record = {"original": identity["id"], "history": history}
        store.setting(history_key, record)
        if memories:
            lookup = TeamHistory(store, scope)
            sources = {}
            items = []
            for item in memories:
                meta = item["metadata"]
                prior = meta.get("fishhome_source_ref")
                previous = prior.get("ref") if isinstance(prior, dict) else None
                if not isinstance(previous, str):
                    previous = None
                if previous not in sources:
                    mapped = lookup.copy_reference(ident, previous)
                    if mapped is None:
                        mapped = f"copy:{ident}:{len(history)}"
                        history.append({"ref": previous or f"memory:{item['original_id']}", "kind": "missing",
                                        "source": {"status": "unavailable", "original_agent_id": identity["id"]}})
                    sources[previous] = mapped
                items.append({**item, "reference": sources[previous]})
            store.setting(history_key, record)
            store.setting(copy_memory_key(scope, ident),
                          {"items": items, "next": 0, "total": len(items), "state": "pending"})
        import_card_files(store, scope, ident, name, card)
    return {**roster.public(row), "history": len(history), "memories": len(memories),
            "import_state": "pending" if memories else "ready"}


def queued_copy(store, scope):
    prefix = f"{COPY_MEMORY_PREFIX}{scope}:"
    row = store.db.execute(
        "SELECT key FROM settings WHERE substr(key,1,?)=? AND json_extract(value,'$.state')='pending' LIMIT 1",
        (len(prefix), prefix)).fetchone()
    return row[0][len(prefix):] if row else None


async def restore_memories(store, scope, agent_id, backend):
    key = copy_memory_key(scope, agent_id)
    job = store.get_setting(key)
    if not job or job["state"] != "pending":
        return
    try:
        for item in job["items"][job["next"]:]:
            meta = item["metadata"]
            source_ref = {"scope": scope, "ref": item["reference"], "imported_memory_id": item["original_id"]}
            args = {"owner": agent_id, "source_ref": source_ref}
            importance = meta.get("importance")
            if type(importance) not in (float, int) or not 0 <= importance <= 1:
                importance = 0.5
            try:
                ident = await backend.save(**args, content=item["text"],
                    actors=meta.get("fishhome_actors") or [], speakers=meta.get("fishhome_speakers") or [],
                    evaluation_author=meta.get("fishhome_evaluation_author"),
                    summary_author=meta.get("fishhome_summary_author"), importance=importance,
                    import_origin={"agent_id": meta["fishhome_owner"], "memory_id": item["original_id"],
                                   "source_ref": meta.get("fishhome_source_ref"),
                                   "saved_from": meta.get("fishhome_saved_from"),
                                   "prior_import": meta.get("fishhome_import"),
                                   "created_at": meta.get("create_time", meta.get("created_at")),
                                   "status": meta.get("status")})
            except MemoryUncertainError:
                recovered = await backend.reconcile(**args)
                if recovered["status"] != "recovered":
                    raise
                ident = recovered["memory_ids"][0]
            verified = await backend.get(owner=agent_id, memory_id=ident)
            if not verified or verified.get("text", verified.get("content")) != item["text"]:
                raise ValueError("导入的记忆未通过 LivingMemory 回读")
            job["next"] += 1
            store.setting(key, job)
        roster = DshRoster(store, scope)
        with store.transaction():
            row = roster.get(agent_id)
            if row["status"] == "paused":
                row.update(status="active", updated=time())
                roster.save(row)
            store.setting(key, {"next": job["next"], "total": job["total"], "state": "ready"})
    except Exception as exc:
        logger.warning("DSH memory import %s stopped at %s: %s", agent_id, job["next"], type(exc).__name__)
        store.setting(key, {**job, "state": "needs_review", "error": "有记忆未完成回读，副本仍暂停。"})


def memories_for(store, agent_id):
    path = store.folder / "dsh_team_memory" / "livingmemory.db"
    if not path.is_file():
        return []
    session_id, persona_id = TeamMemoryBackend.agent_scope(agent_id)
    adapter = TeamMemoryBackend._adapter(agent_id, None)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        records = db.execute(
            "SELECT id,text,metadata FROM documents WHERE json_extract(metadata,'$.fishhome_owner')=? "
            "AND json_extract(metadata,'$.session_id')=? AND json_extract(metadata,'$.persona_id')=? "
            "AND json_extract(metadata,'$.adapter_id')=? ORDER BY id",
            (agent_id, session_id, persona_id, adapter),
        )
        return [{"original_id": ident, "text": text, "metadata": json.loads(metadata)}
                for ident, text, metadata in records]


def snapshot(store, scope, agent_id):
    roster = DshRoster(store, scope)
    agent = roster.get(agent_id)
    if import_status(store, scope, agent_id)["state"] != "ready":
        raise ValueError("请等副本记忆导入完成后再导出经历档案")
    profile = agent["profiles"][-1]
    card = export_card(store, agent_id, profile["name"], profile["persona"])
    history = TeamHistory(store, scope).authored_records(agent_id)
    memories = memories_for(store, agent_id)
    contents = {"format": HISTORY_FORMAT, "agent_id": agent_id, "name": agent["name"],
                "records": len(history), "memories": len(memories)}

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("role.zip", card)
        for path, value in (("archive.json", contents), ("identity.json", agent),
                            ("history.json", history), ("memories.json", memories)):
            archive.writestr(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        archive.writestr("README.md",
                         "# 小弟经历档案\n\n"
                         "identity.json 保存身份、人格版本和逻辑会话指针。\n"
                         "history.json 保存 FishHome 中已有的聊天、任务和组会记录。\n"
                         "memories.json 保存该小弟自己的记忆正文与来源。\n"
                         "role.zip 是可单独导入的角色卡。\n"
                         "在 FishHome 控制台导入为副本会分配新的小弟和 DSH 会话。\n")
        if sum(entry.file_size for entry in archive.infolist()) > MAX_ARCHIVE:
            raise ValueError("该小弟的经历档案超过 32 MiB")
    return output.getvalue()
