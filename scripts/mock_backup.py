"""M1-only snapshot and restore for explicitly selected mock SQLite data."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

FORMAT = "fish-home-m1-mock-sqlite-v1"
DATABASE = "mock.sqlite3"
MANIFEST = "manifest.json"


def read_only(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_database(path):
    with closing(read_only(path)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


@contextmanager
def staged_directory(destination):
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=destination.name + ".partial-", dir=destination.parent
    ) as work:
        stage = Path(work) / "ready"
        stage.mkdir()
        yield stage
        stage.rename(destination)


def snapshot(source, archive):
    if not source.is_file():
        raise FileNotFoundError(f"mock database not found: {source}")
    with staged_directory(archive) as stage:
        database = stage / DATABASE
        with closing(read_only(source)) as original, closing(
            sqlite3.connect(database)
        ) as copy:
            original.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")
        check_database(database)
        manifest = {"format": FORMAT, "sha256": checksum(database)}
        (stage / MANIFEST).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Snapshot saved: {archive}")


def verify(archive):
    manifest = json.loads((archive / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("not an M1 mock archive")
    database = archive / DATABASE
    if checksum(database) != manifest.get("sha256"):
        raise ValueError("archive checksum mismatch")
    check_database(database)
    return manifest


def restore(archive, destination, apply):
    manifest = verify(archive)
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if not apply:
        print(f"Dry run: verified {archive}; would restore to {destination}")
        return
    with staged_directory(destination) as stage:
        shutil.copyfile(archive / DATABASE, stage / DATABASE)
        if checksum(stage / DATABASE) != manifest["sha256"]:
            raise ValueError("copied snapshot checksum mismatch")
    print(f"Restored: {destination / DATABASE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    capture.add_argument("source", type=Path)
    capture.add_argument("archive", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    recover = commands.add_parser("restore")
    recover.add_argument("archive", type=Path)
    recover.add_argument("destination", type=Path)
    recover.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    archive = options.archive.resolve()
    try:
        if options.command == "snapshot":
            snapshot(options.source.resolve(), archive)
        elif options.command == "verify":
            verify(archive)
            print(f"Verified: {archive}")
        else:
            restore(archive, options.destination.resolve(), options.apply)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
