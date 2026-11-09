"""Local export, room snapshots and migration previews for FishHome."""

import json
import os
import re
import shutil
import socket
import tempfile
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from deploy.memory_observatory.activity_reader import read_activity
from deploy.memory_observatory.livingmemory_reader import limited_summary, read_snapshot, scope_key, version_from_file

from .mock_backup import read_only, staged_directory
from .native_archive import ACTIVITY, LM, restore, utc_now, verify


ROOM_FORMAT = "fishhome-room-v1"


def sources(root):
    memory = root / LM / "livingmemory.db"
    metadata = root / "data/plugins/astrbot_plugin_livingmemory/metadata.yaml"
    activity = (root / ACTIVITY).parent
    return memory, metadata, activity


def room_activity(snapshot):
    for group, keys in (("goals", ("title", "why", "next_step")), ("decisions", ("reason", "topic")),
                        ("works", ("title",))):
        for row in snapshot.get(group, []):
            for key in keys:
                row[key] = limited_summary(row.get(key), 100)
    for project in snapshot.get("projects", {}).get("items", []):
        for key in ("title", "why"):
            project[key] = limited_summary(project.get(key), 100)
        for stage in project.get("stages", []):
            for key in ("title", "next_step"):
                stage[key] = limited_summary(stage.get(key), 100)
            if stage.get("work"):
                stage["work"]["title"] = limited_summary(stage["work"].get("title"), 100)
                stage["work"]["excerpt"] = ""
            if stage.get("review"):
                for key in ("reflection", "limitations", "next_question"):
                    stage["review"][key] = limited_summary(stage["review"].get(key), 100)
    return snapshot


def room_bundle(root):
    memory, metadata, activity = sources(root)
    return {"format": ROOM_FORMAT, "connection": "ready", "source": socket.gethostname(), "captured_at": utc_now(),
            "memory": read_snapshot(memory, version_from_file(metadata), summaries_only=True),
            "activity": room_activity(read_activity(activity))}


def write_bundle(destination, bundle):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".partial-",
                                     delete=False, mode="w", encoding="utf-8") as stream:
        partial = stream.name
        try:
            json.dump(bundle, stream, ensure_ascii=False, allow_nan=False)
            stream.close()
            os.replace(partial, destination)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)


def sync_room(destination, fetch):
    try:
        bundle = fetch()
        if (bundle.get("format") != ROOM_FORMAT or bundle.get("connection") != "ready"
                or bundle.get("memory", {}).get("schema") != "memory-observatory/v1"
                or bundle.get("activity", {}).get("schema") != "dafeiyu-room/v1"):
            raise ValueError("unrecognized room data")
        write_bundle(destination, bundle)
        return bundle
    except Exception:
        if destination.is_file():
            old = json.loads(destination.read_text(encoding="utf-8"))
            if old.get("format") == ROOM_FORMAT:
                write_bundle(destination, {**old, "connection": "stale", "failed_at": utc_now()})
        raise


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def private_messages(root, activity):
    cfg = json.loads((activity / "control.json").read_text(encoding="utf-8-sig"))
    session = cfg.get("owner_scope")
    if not isinstance(session, str) or not re.fullmatch(r"[^:]+:FriendMessage:\d+", session):
        raise ValueError("owner conversation is not configured")
    with closing(read_only((root / LM / "conversations.db").resolve(strict=True))) as db:
        for ident, role, content, at in db.execute(
            "SELECT id,role,content,timestamp FROM messages WHERE session_id=? ORDER BY timestamp,id", (session,)
        ):
            yield {"id": ident, "scope": scope_key(session, None), "role": role, "content": content, "timestamp": at}


def export_memory(root, destination, *, include_private_chat=False):
    memory, metadata, activity = sources(root)
    raw = read_snapshot(memory, version_from_file(metadata), limit=None)
    projects = read_activity(activity)["projects"]
    with staged_directory(destination) as stage:
        write_jsonl(stage / "memory.jsonl", raw["memories"])
        write_jsonl(stage / "projects.jsonl", projects["items"])
        if include_private_chat:
            write_jsonl(stage / "private-chat.jsonl", private_messages(root, activity))
        info = {"format": "fishhome-readable-v1", "source": socket.gethostname(), "as_of": raw["as_of"],
                "livingmemory": raw["source_info"], "project_total": projects["total"],
                "project_items": len(projects["items"]), "private_chat": include_private_chat}
        (stage / "manifest.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "README.md").write_text("# FishHome 可读导出\n\n"
            "`memory.jsonl` 是当前已建立范围的活跃记忆投影，`projects.jsonl` 是原版项目读出。"
            "遗漏、截断与投影时间见 `manifest.json`。数据库恢复使用另行生成的原生档案。\n", encoding="utf-8")
        items = [f"- {item['id']} · {limited_summary(item['title'], 64)}" for item in raw["memories"]]
        (stage / "INDEX.md").write_text("# 记忆索引\n\n" + "\n".join(items) + "\n", encoding="utf-8")
    return info


def preview_migration(mode, archive=None, *, selection=(), destination=None):
    if mode == "none":
        return {"mode": mode, "records": 0, "target_changed": False}
    if archive is None:
        raise ValueError("selective or full migration requires a verified source archive")
    manifest = verify(archive)
    if mode == "full":
        if manifest["profile"] != "runtime" or manifest["consistency"] != "writers-stopped":
            raise ValueError("full migration requires a stopped-writer runtime archive")
        if destination:
            restore(archive, destination)
        return {"mode": mode, "source": manifest["environment"], "files": len(manifest["files"]),
                "private_chat": manifest["include_private_chat"], "target_changed": False}
    if mode != "selective" or not selection or any(not re.fullmatch(r"lm-(?:doc|atom)-[1-9]\d*", value) for value in selection):
        raise ValueError("selective migration requires explicit LivingMemory record IDs")
    with ZipFile(archive) as bundle, tempfile.TemporaryDirectory(prefix="fishhome-migration-") as folder:
        path = Path(folder) / "livingmemory.db"
        with bundle.open(f"{LM}/livingmemory.db") as stream, path.open("wb") as target:
            shutil.copyfileobj(stream, target)
        records = []
        with closing(read_only(path)) as db:
            for record in dict.fromkeys(selection):
                _, kind, ident = record.split("-")
                query = ("SELECT id FROM documents WHERE id=?" if kind == "doc" else
                         "SELECT parent_memory_id FROM memory_atoms WHERE id=?")
                row = db.execute(query, (int(ident),)).fetchone()
                if row is None:
                    raise ValueError(f"unknown LivingMemory record: {record}")
                records.append({"id": record, "parent": f"lm-doc-{row[0]}" if kind == "atom" else None})
    return {"mode": mode, "source": manifest["environment"], "records": records, "target_changed": False}
