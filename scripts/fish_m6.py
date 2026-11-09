"""FishHome Windows data operations and release previews."""

import argparse
import base64
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path, PureWindowsPath
from zipfile import BadZipFile

from .m6_data import export_memory, preview_migration, room_bundle, sync_room
from .native_archive import restore, snapshot, verify


REPO = Path(__file__).resolve().parents[1]
PORTS = {"astrbot": 6185, "napcat": 6099, "manager": 6196, "budget": 6197,
         "phone": 8009, "bili": 8011, "room": 8767, "ollama": 11434, "dsh": 59934}


def reachable(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.4):
            return True
    except OSError:
        return False


def status():
    return {"host": socket.gethostname(), "components": {name: {"port": port, "reachable": reachable(port)}
             for name, port in PORTS.items()}}


def remote_command(repo, python, args):
    if not all(PureWindowsPath(path).is_absolute() for path in (repo, python)):
        raise ValueError("supply absolute Windows paths for the ThinkBook repository and Python")
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    argv = " ".join(map(quote, args))
    script = ("$ErrorActionPreference = 'Stop'; $env:PYTHONUTF8 = '1'; "
              f"Set-Location -LiteralPath {quote(repo)}; & {quote(python)} -B -m scripts.fish_m6 {argv}; "
              "exit $LASTEXITCODE")
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + base64.b64encode(script.encode("utf-16le")).decode()


def remote_json(repo, python, args, run=subprocess.run):
    ssh = shutil.which("ssh") or "ssh"
    command = [ssh, "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
               "fishbook", remote_command(repo, python, args)]
    result = run(command, capture_output=True, timeout=45)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise OSError("ThinkBook SSH or the read-only command failed; check the pinned host key and remote paths")
    return json.loads(result.stdout)


def release_plan(revision, destination=None, *, apply=False):
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise ValueError("specify the full tested Git commit")
    commit = subprocess.check_output(["git", "rev-parse", "--verify", revision + "^{commit}"],
                                     cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
    if commit != revision:
        raise ValueError("release ref does not identify the requested commit")
    if apply:
        if destination is None or destination.exists():
            raise ValueError("a new local release ZIP path is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fishhome-release-", dir=destination.parent) as work:
            bundle = Path(work) / "release.zip"
            subprocess.run(["git", "archive", "--format=zip", f"--output={bundle}", commit], cwd=REPO, check=True)
            if destination.exists():
                raise FileExistsError("release already exists")
            bundle.rename(destination)
    return {"revision": commit, "action": "local-source-archive" if apply else "preview", "memory_migration": "none",
            "destination": str(destination) if destination else None, "production_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "backup", "verify", "restore", "export-memory", "room-sync",
                                            "room-bundle", "migration-preview", "deploy"))
    parser.add_argument("--target", choices=("local-dev", "fishbook"), default="local-dev")
    parser.add_argument("--astrbot-root", type=Path)
    parser.add_argument("--remote-repo")
    parser.add_argument("--remote-python")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--ref")
    parser.add_argument("--environment", choices=("dev", "prod"), default="dev")
    parser.add_argument("--profile", choices=("runtime", "memory"), default="runtime")
    parser.add_argument("--mode", choices=("none", "selective", "full"), default="none")
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--work-file", action="append", default=[])
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--include-private-chat", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root, archive, destination = args.astrbot_root, args.archive, args.destination
    try:
        if args.target == "fishbook" and args.command not in {"status", "room-sync", "deploy"}:
            raise ValueError("run this data operation on the source host; remote transfer requires a separate approved path")
        if args.target == "fishbook" and args.command != "deploy" and not (args.remote_repo and args.remote_python):
            raise ValueError("configure the ThinkBook repository and Python paths")
        if args.command in {"backup", "export-memory", "room-sync", "room-bundle"} and not root:
            raise ValueError("specify the AstrBot root")
        if args.command in {"backup", "verify", "restore"} and not archive:
            raise ValueError("specify an archive path")
        if args.command in {"restore", "export-memory", "room-sync"} and not destination:
            raise ValueError("specify a destination path")
        if args.command == "status":
            result = (remote_json(args.remote_repo, args.remote_python, ["status"])
                      if args.target == "fishbook" else status())
        elif args.command == "backup":
            if args.writers_stopped:
                producers = [PORTS["astrbot"]]
                if args.profile == "runtime":
                    producers.append(PORTS["budget"])
                if args.workspace:
                    producers.append(PORTS["dsh"])
                if any(reachable(port) for port in producers):
                    raise ValueError("stop the selected data writers before a stopped-writer archive")
            result = snapshot(root, archive, environment=args.environment, revision=args.ref or "", profile=args.profile,
                              include_private_chat=args.include_private_chat, writers_stopped=args.writers_stopped,
                              workspace=args.workspace, work_files=args.work_file)
            result = {"archive": str(archive), "files": len(result["files"]), "consistency": result["consistency"]}
        elif args.command == "verify":
            result = {"archive": str(archive), "verified": True, "files": len(verify(archive)["files"])}
        elif args.command == "restore":
            result = {"destination": str(destination), "applied": args.apply,
                      "files": len(restore(archive, destination, apply=args.apply)["files"])}
        elif args.command == "export-memory":
            info = export_memory(root, destination, include_private_chat=args.include_private_chat)
            result = {"destination": str(destination), "memory": info["livingmemory"]["counts"],
                      "projects": info["project_items"], "private_chat": info["private_chat"]}
        elif args.command == "room-bundle":
            result = room_bundle(root)
        elif args.command == "room-sync":
            def fetch():
                if args.target == "fishbook":
                    return remote_json(args.remote_repo, args.remote_python, ["room-bundle", "--astrbot-root", str(root)])
                return room_bundle(root)
            bundle = sync_room(destination, fetch)
            result = {"destination": str(destination), "captured_at": bundle["captured_at"], "source": bundle["source"]}
        elif args.command == "migration-preview":
            result = preview_migration(args.mode, archive, selection=args.record, destination=destination)
        else:
            result = release_plan(args.ref, destination, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, KeyError, sqlite3.Error, BadZipFile, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
