"""Select the task and meeting group for the configured FishHome persona."""

import json
from pathlib import Path


GROUP_SQL = "AND (? IS NULL OR coalesce(json_extract(value,'$.persona_mode'),'test')=?) "
MODES = {"formal", "test"}


def configured_mode(path):
    try:
        config = Path(path).read_text(encoding="utf-8-sig")
        runner = json.loads(config).get("agent_runner", {})
        options = runner.get("config", {})
        selected = options.get("persona", {}) if runner.get("runner_type") == "local" else options
        persona = selected.get("persona_id")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if isinstance(persona, str):
        if persona.startswith("feiyu"):
            return "formal"
        if persona.startswith("fish-home-"):
            return "test"
    return None


def store_mode(store):
    folder = getattr(store, "folder", None)
    return configured_mode(folder.parent.parent / "cmd_config.json") if isinstance(folder, Path) else None


def visible(row, mode):
    return bool(row) and (mode is None or (row.get("persona_mode") or "test") == mode)


def latest_team_notice(store, scope):
    notice = store.get_setting("dsh-team-latest:" + scope)
    mode = store_mode(store)
    if not notice or mode is None:
        return notice
    if notice.get("persona_mode") in MODES:
        return notice if visible(notice, mode) else None
    reference = notice.get("reference")
    if reference:
        for prefix in ("dsh-meeting:v1:", "commissioned-file:v1:"):
            if row := store.get_setting(f"{prefix}{scope}:{reference}"):
                return notice if visible(row, mode) else None
    return notice
