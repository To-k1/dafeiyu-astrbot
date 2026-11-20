"""Switch the owner chat and the personal activity model together."""

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from plugin.astrbot_plugin_bili_activity.model_route import (
    GO_PROVIDER, OFFICIAL_AUTONOMOUS, OFFICIAL_INTERACTION_BASE, is_go_config,
)

from .persona_mode import read_owner_binding
from .windows import atomic_write, write_json


PREFERENCE = "provider_perf_chat_completion"
GO_KEY_ENV = "FISHHOME_OPENCODE_GO_KEY"
CONTROL = "data/plugin_data/astrbot_plugin_bili_activity/control.json"


def configured_routes(cfg):
    sources = {source["id"]: source for source in cfg.get("provider_sources", [])}
    routes = {}
    for model in cfg.get("provider", []):
        source = sources.get(model.get("provider_source_id"), {})
        if not model.get("enable") or not source.get("enable", True):
            continue
        config = {**source, **model}
        if model["id"] == GO_PROVIDER and is_go_config(config) and config.get("key"):
            routes["go"] = {"chat": model["id"], "autonomous": GO_PROVIDER}
        if (config.get("model") == "deepseek-flash"
                and str(config.get("api_base", "")).rstrip("/") == OFFICIAL_INTERACTION_BASE):
            routes["deepseek"] = {"chat": model["id"], "autonomous": OFFICIAL_AUTONOMOUS}
    return routes


def load(astro):
    cfg = json.loads((astro / "data/cmd_config.json").read_text(encoding="utf-8-sig"))
    control = json.loads((astro / CONTROL).read_text(encoding="utf-8-sig"))
    return cfg, control


def status(astro, scope):
    if not scope or not (astro / CONTROL).is_file():
        return {"mode": "unavailable", "routes": []}
    cfg, control = load(astro)
    routes = configured_routes(cfg)
    core = astro / "data/data_v4.db"
    with closing(sqlite3.connect(core.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("""SELECT json_extract(value,'$.val') FROM preferences
            WHERE (scope='umo' AND scope_id=? AND key=?)
               OR (scope='global' AND scope_id='global' AND key='curr_provider')
            ORDER BY scope='umo' DESC LIMIT 1""", (scope, PREFERENCE)).fetchone()
    chat = (row[0] if row else None) or cfg["agent_runner"]["config"].get("model", {}).get("provider_id")
    autonomous = control.get("autonomous_provider_id")
    mode = next((name for name, route in routes.items()
                 if route == {"chat": chat, "autonomous": autonomous}), "mixed")
    return {"mode": mode, "routes": list(routes), "chat": chat, "autonomous": autonomous}


def resolve(astro, scope, mode):
    read_owner_binding(astro, scope)
    cfg, control = load(astro)
    route = configured_routes(cfg).get(mode)
    if route is None:
        raise ValueError("请先在 AstrBot 启用对应的模型提供商")
    model = cfg["agent_runner"]["config"].get("model", {})
    if mode == "go" and any(pid != GO_PROVIDER for pid in model.get("fallback_provider_ids") or []):
        raise ValueError("请在 AstrBot 清除其他模型回退，避免 Go 到限后产生按量费用")
    return route, cfg, control


def bind_go_key(cfg):
    model = next(model for model in cfg["provider"] if model["id"] == GO_PROVIDER)
    source_id = model.get("provider_source_id")
    if source_id:
        target = next(source for source in cfg["provider_sources"] if source["id"] == source_id)
    else:
        target = model
    target["key"] = ["${" + GO_KEY_ENV + "}"]


async def activate(astro, scope, mode):
    route, cfg, control = resolve(astro, scope, mode)
    os.environ["ASTRBOT_ROOT"] = str(astro)
    from astrbot.core import db_helper, sp

    previous = await sp.get_async("umo", scope, PREFERENCE, None)
    try:
        await sp.session_put(scope, PREFERENCE, route["chat"])
        settings = astro / "data/cmd_config.json"
        original = settings.read_bytes() if mode == "go" else None
        try:
            if original is not None:
                bind_go_key(cfg)
                write_json(settings, cfg)
            write_json(astro / CONTROL, {**control, "autonomous_provider_id": route["autonomous"]})
        except OSError:
            if original is not None:
                atomic_write(settings, original)
            if previous is None:
                await sp.remove_async("umo", scope, PREFERENCE)
            else:
                await sp.session_put(scope, PREFERENCE, previous)
            raise
    finally:
        await sp.close()
        await db_helper.engine.dispose()


if __name__ == "__main__":
    directory, scope, mode = sys.argv[1:]
    asyncio.run(activate(Path(directory), scope, mode))
