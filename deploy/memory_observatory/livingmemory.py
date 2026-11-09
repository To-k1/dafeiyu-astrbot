"""Read-only sources configured at startup, never by HTTP."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .livingmemory_reader import SourceError, read_snapshot, version_from_file
from .model import normalize


class LocalMemorySource:
    def __init__(self, astrbot_root: Path):
        data = astrbot_root / "data"
        plugin = "astrbot_plugin_livingmemory"
        self.db = data / "plugin_data" / plugin / "livingmemory.db"
        self.metadata = data / "plugins" / plugin / "metadata.yaml"

    def load(self):
        try:
            raw = read_snapshot(self.db, version_from_file(self.metadata))
            snapshot = normalize(raw, allow_empty=True)
            info = {**raw["source_info"], "enabled": True, "state": "ready"}
            return snapshot, info
        except (OSError, ValueError, sqlite3.Error):
            raise SourceError("无法读取本机 LivingMemory；请检查路径、版本和表结构。") from None


def read_room_bundle(path):
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("room cache exceeds the size limit")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle.get("format") != "fishhome-room-v1" or bundle.get("connection") not in {"ready", "stale"}:
            raise ValueError("unsupported room cache")
        capture = datetime.fromisoformat(bundle["captured_at"])
        age = (datetime.now(timezone.utc) - capture).total_seconds()
        if age < -120:
            raise ValueError("room cache clock is ahead")
        state = "stale" if age > 180 or bundle["connection"] == "stale" else "ready"
        return bundle, state, max(0, age)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SourceError("无法读取已同步的房间缓存。") from None


class FileMemorySource:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        bundle, state, _ = read_room_bundle(self.path)
        try:
            raw = bundle["memory"]
            return normalize(raw, allow_empty=True), {**raw["source_info"], "enabled": True, "state": state}
        except (KeyError, ValueError, TypeError):
            raise SourceError("缓存中的记忆快照不可用。") from None


class SSHMemorySource:
    def __init__(self, *, host=None, key=None, run=subprocess.run):
        if host is None:
            host = os.environ.get("DAFEIYU_SSH_HOST", "").strip()
            if not host:
                config = Path.home() / ".dafeiyu-admin" / "host"
                if config.is_file():
                    host = config.read_text(encoding="utf-8").strip()
        if not host or host.startswith("-") or any(c.isspace() or c in ";|&`$\"'\\" for c in host):
            raise SourceError("Configure DAFEIYU_SSH_HOST or ~/.dafeiyu-admin/host")
        self.command = [shutil.which("ssh") or "ssh", "-i", str(key or Path.home() / ".ssh" / "ecs_dafeiyu"),
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
                        host, "python3 -"]
        self.run = run

    def load(self):
        source = Path(__file__).with_name("livingmemory_reader.py").read_text(encoding="utf-8")
        try:
            result = self.run(self.command, input=source, capture_output=True, text=True, encoding="utf-8",
                              errors="strict", timeout=40, **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            if result.returncode or len(result.stdout.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("source failed")
            envelope = json.loads(result.stdout)
            if envelope.get("ok") is not True:
                raise ValueError("source failed")
            raw = envelope["snapshot"]
            snapshot = normalize(raw, allow_empty=True)
            info = raw["source_info"]
            info["enabled"], info["state"] = True, "ready"
            return snapshot, info
        except Exception:
            raise SourceError("无法读取 LivingMemory；请检查 SSH、版本和表结构。上次快照未修改。") from None
