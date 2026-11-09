"""Native Windows data archives and isolated recovery for FishHome."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from .mock_backup import check_database, checksum, read_only, staged_directory


FORMAT = "fishhome-native-v1"
LM = "data/plugin_data/astrbot_plugin_livingmemory"
ACTIVITY = "data/plugin_data/astrbot_plugin_bili_activity/activity.sqlite3"
SQLITE = {f"{LM}/livingmemory.db", f"{LM}/livingmemory_graph_documents.db", ACTIVITY}
# AstrBot's core DB also stores API keys and trusted-device credentials.
CHAT = {f"{LM}/conversations.db"}
COMPANIONS = {f"{LM}/livingmemory.index", f"{LM}/livingmemory_graph.index", f"{LM}/decay_state.json"}
MANIFEST = "manifest.json"
PRIVATE_NAME = re.compile(r"credential|secret|cookie|token|passwor[dt]|login|session|\.env", re.I)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_parts(value):
    path = PurePosixPath(value)
    if (not path.parts or str(path) != value or path.is_absolute() or ".." in path.parts
            or "\\" in value or any(re.search(r'[<>:"|?*\x00-\x1f]|[. ]$', p) for p in path.parts)
            or any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I) for p in path.parts)):
        raise ValueError("invalid archive path")
    return path.parts


def workspace_parts(value):
    parts = path_parts(value)
    if (any(part.startswith(".") or PRIVATE_NAME.search(part) for part in parts)
            or PurePosixPath(value).suffix.lower() not in {".md", ".txt", ".csv", ".json", ".jsonl"}):
        raise ValueError("workspace selection must be an explicit non-credential text artifact")
    return parts


def regular_file(root, parts):
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=True)
    if resolved != candidate or not resolved.is_file():
        raise ValueError("source must be a regular file under the declared root")
    return resolved


def copy_sqlite(source, target):
    with closing(read_only(source)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")
        version = copy.execute("PRAGMA user_version").fetchone()[0]
    check_database(target)
    return version


def required_files(profile, private_chat):
    required = {f"{LM}/livingmemory.db"}
    if profile == "runtime":
        required.add(ACTIVITY)
    if private_chat:
        required.update(CHAT)
    return required


def verify(archive):
    with ZipFile(archive) as file:
        info = file.getinfo(MANIFEST)
        if info.file_size > 1024 * 1024:
            raise ValueError("manifest is too large")
        manifest = json.loads(file.read(info))
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("not a FishHome native archive")
        if (manifest.get("environment") not in {"dev", "prod"}
                or manifest.get("profile") not in {"memory", "runtime"}
                or type(manifest.get("include_private_chat")) is not bool
                or manifest.get("consistency") not in {"sqlite-online", "writers-stopped"}
                or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("revision", "")))):
            raise ValueError("invalid native archive manifest")
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("archive has no file inventory")
        names = {MANIFEST.casefold()}
        expected = {MANIFEST}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError("invalid file inventory")
            name = row["path"]
            path_parts(name)
            if name.casefold() in names:
                raise ValueError("duplicate archive path")
            names.add(name.casefold())
            expected.add(name)
            allowed = (name in SQLITE and (manifest["profile"] == "runtime" or name != ACTIVITY)
                       or (manifest["include_private_chat"] and name in CHAT))
            if manifest["consistency"] == "writers-stopped":
                allowed = allowed or name in COMPANIONS
                if manifest["profile"] == "runtime" and name.startswith("workspace/"):
                    workspace_parts(name.removeprefix("workspace/"))
                    allowed = True
            if not allowed or row.get("kind") != ("sqlite" if name in SQLITE | CHAT else "file"):
                raise ValueError("unexpected archive member")
            entry = file.getinfo(name)
            if type(row.get("bytes")) is not int or row["bytes"] != entry.file_size:
                raise ValueError("archive member size mismatch")
            with file.open(entry) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != row.get("sha256"):
                    raise ValueError("archive member checksum mismatch")
        if len(file.infolist()) != len(expected) or set(file.namelist()) != expected:
            raise ValueError("archive members do not match its manifest")
        if not required_files(manifest["profile"], manifest["include_private_chat"]) <= expected:
            raise ValueError("one of the required native databases is missing")
    return manifest


def snapshot(root, archive, *, environment, revision, include_private_chat=False,
             writers_stopped=False, workspace=None, work_files=(), profile="runtime"):
    if (environment not in {"dev", "prod"} or profile not in {"memory", "runtime"}
            or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("supply the environment and a full release commit")
    if archive.exists():
        raise FileExistsError("archive already exists")
    if (workspace or work_files) and (profile != "runtime" or not writers_stopped or not workspace or not work_files):
        raise ValueError("workspace files require a selected root, filenames and stopped writers")
    root = root.resolve(strict=True)
    sources = {}
    databases = SQLITE if profile == "runtime" else SQLITE - {ACTIVITY}
    for name in sorted(databases | (CHAT if include_private_chat else set()) | (COMPANIONS if writers_stopped else set())):
        parts = path_parts(name)
        if root.joinpath(*parts).exists():
            sources[name] = regular_file(root, parts)
    if not required_files(profile, include_private_chat) <= sources.keys():
        raise ValueError("one of the required native databases is missing")
    if workspace:
        work_root = workspace.resolve(strict=True)
        for name in work_files:
            sources[f"workspace/{name}"] = regular_file(work_root, workspace_parts(name))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=archive.name + ".partial-", dir=archive.parent) as work:
        stage = Path(work)
        bundle = stage / "archive.zip"
        manifest = {"format": FORMAT, "environment": environment, "revision": revision, "profile": profile,
                    "source_host": socket.gethostname(), "started_at": utc_now(),
                    "include_private_chat": include_private_chat,
                    "consistency": "writers-stopped" if writers_stopped else "sqlite-online", "files": []}
        stamps = {name: (path.stat().st_size, path.stat().st_mtime_ns) for name, path in sources.items()}
        with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as file:
            for name, source in sources.items():
                data = stage / "file"
                sqlite = name in SQLITE | CHAT
                details = {"user_version": copy_sqlite(source, data)} if sqlite else {}
                if not sqlite:
                    shutil.copyfile(source, data)
                manifest["files"].append({"path": name, "kind": "sqlite" if sqlite else "file",
                                          "bytes": data.stat().st_size, "sha256": checksum(data), **details})
                file.write(data, name)
                data.unlink()
            if writers_stopped and any((path.stat().st_size, path.stat().st_mtime_ns) != stamps[name]
                                       for name, path in sources.items()):
                raise ValueError("a source changed during the stopped-writer capture")
            manifest["finished_at"] = utc_now()
            file.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        verify(bundle)
        if archive.exists():
            raise FileExistsError("archive already exists")
        bundle.rename(archive)
    return manifest


def restore(archive, destination, *, apply=False):
    manifest = verify(archive)
    if destination.exists():
        raise FileExistsError("restoration requires a new isolated directory")
    if not apply:
        return manifest
    with staged_directory(destination) as stage, ZipFile(archive) as file:
        for row in manifest["files"]:
            name = row["path"]
            target = stage.joinpath(*path_parts(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            with file.open(name) as source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
            if checksum(target) != row["sha256"]:
                raise ValueError("restored member checksum mismatch")
            if row["kind"] == "sqlite":
                check_database(target)
        (stage / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
