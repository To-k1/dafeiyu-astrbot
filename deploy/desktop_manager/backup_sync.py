"""Online ThinkBook backups and verified copies on the desktop."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4
from zipfile import BadZipFile
from zoneinfo import ZoneInfo

from scripts.native_archive import snapshot, verify


PREFIX = "thinkbook-backup"
NAME = re.compile(r"thinkbook-backup-(?:daily-)?\d{8}T\d{6}\.\d{6}Z\.zip")
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def archive_stamp(name):
    return name.removeprefix(PREFIX + "-").removeprefix("daily-")


def archive_now(astro, backups, *, desktop, revision=None, daily=False):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    name = f"{'desktop-backup' if desktop else PREFIX}-{'daily-' if daily else ''}{stamp}.zip"
    path = backups / name
    snapshot(astro, path, environment="dev" if desktop else "prod", revision=revision)
    return path


def sync(backups):
    from .thinkbook import select_root, ssh_args
    from .windows import write_json

    script = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" + select_root() +
              "$folder=Join-Path $root 'backups';$names=@(Get-ChildItem -LiteralPath $folder -File "
              "-Filter 'thinkbook-backup-*.zip' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name);"
              "[pscustomobject]@{root=$folder;names=$names}|ConvertTo-Json -Compress")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run([*ssh_args(), "fishbook", "powershell.exe", "-NoProfile", "-NonInteractive",
                             "-EncodedCommand", encoded], capture_output=True, timeout=20, creationflags=HIDDEN)
    if result.returncode:
        raise OSError("ThinkBook 备份目录暂不可读")
    inventory = json.loads(result.stdout)
    source = inventory["root"].replace("\\", "/")
    if not re.fullmatch(r"[A-Za-z]:/[\w./ -]+", source) or not source.endswith("/backups"):
        raise ValueError("ThinkBook 返回了无效的备份路径")
    backups.mkdir(parents=True, exist_ok=True)
    if not isinstance(inventory["names"], list) or not all(isinstance(name, str) for name in inventory["names"]):
        raise ValueError("ThinkBook 返回了无效的备份清单")
    latest = None
    for name in sorted(set(inventory["names"]), key=archive_stamp):
        if not NAME.fullmatch(name):
            continue
        target = backups / name
        try:
            manifest = verify(target) if not target.is_symlink() else None
        except (OSError, ValueError, KeyError, BadZipFile):
            manifest = None
        if not manifest or manifest["environment"] != "prod":
            partial = backups / (uuid4().hex + ".partial")
            try:
                result = subprocess.run([shutil.which("scp") or "scp", "-q", "-o", "BatchMode=yes", "-o",
                                         "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
                                         f"fishbook:{source}/{name}", str(partial)], capture_output=True,
                                        timeout=180, creationflags=HIDDEN)
                if result.returncode or verify(partial)["environment"] != "prod":
                    raise OSError("ThinkBook 备份传输或校验失败")
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        latest = name
    if latest:
        pointer = backups / "thinkbook-last-verified.json"
        if pointer.is_file():
            previous = json.loads(pointer.read_text(encoding="utf-8"))
            if previous.get("name") == latest:
                return latest
            if isinstance(previous.get("name"), str) and NAME.fullmatch(previous["name"]):
                if archive_stamp(previous["name"]) > archive_stamp(latest) and (backups / previous["name"]).is_file():
                    return previous["name"]
        write_json(pointer, {"name": latest, "verified_at": datetime.now(timezone.utc).isoformat()})
    return latest


def rotate(backups):
    now = datetime.now(timezone.utc).date()
    kept = set()
    pointer = backups / "thinkbook-last-verified.json"
    latest = json.loads(pointer.read_text(encoding="utf-8")).get("name") if pointer.is_file() else None
    for path in sorted(backups.glob(f"{PREFIX}-daily-*.zip"), reverse=True):
        if not NAME.fullmatch(path.name) or path.is_symlink() or path.name == latest:
            continue
        try:
            day = datetime.strptime(archive_stamp(path.name)[:8], "%Y%m%d").date()
        except ValueError:
            continue
        age = (now - day).days
        if age < 7:
            continue
        if age < 35:
            group = ("week", *day.isocalendar()[:2])
        elif (now.year - day.year) * 12 + now.month - day.month < 3:
            group = ("month", day.year, day.month)
        else:
            group = None
        if group is None or group in kept:
            path.unlink(missing_ok=True)
        else:
            kept.add(group)


def daily_backup(astro):
    backups = astro.parent.parent / "backups"
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for path in backups.glob(f"{PREFIX}-*.zip"):
        if NAME.fullmatch(path.name) and datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Shanghai")).date() == today:
            try:
                if verify(path)["environment"] == "prod":
                    return path
            except (OSError, ValueError, KeyError, BadZipFile):
                continue
    path = archive_now(astro, backups, desktop=False, daily=True)
    rotate(backups)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("daily", "pull"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.operation == "pull":
        name = sync(args.path)
        if name:
            rotate(args.path)
        print(name or "No ThinkBook backup available")
    else:
        print(daily_backup(args.path))


if __name__ == "__main__":
    main()
