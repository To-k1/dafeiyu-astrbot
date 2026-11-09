import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from scripts.m6_data import preview_migration, sync_room
from scripts.native_archive import ACTIVITY, CHAT, LM, restore, snapshot, verify


class NativeArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "source"
        self.memory = f"{LM}/livingmemory.db"
        self.archive = self.root / "archive.zip"
        for name in (self.memory, ACTIVITY, *CHAT, "data/data_v4.db"):
            path = self.data / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO documents VALUES (1)")

    def create(self, **kwargs):
        return snapshot(self.data, self.archive, environment="dev", revision="a" * 40, **kwargs)

    def test_online_backup_reads_committed_wal_and_excludes_private_data(self):
        with closing(sqlite3.connect(self.data / self.memory)) as live:
            live.execute("PRAGMA journal_mode=WAL")
            with live:
                live.execute("INSERT INTO documents VALUES (2)")
            live.execute("INSERT INTO documents VALUES (3)")
            manifest = self.create()
        self.assertEqual({row["path"] for row in manifest["files"]}, {self.memory, ACTIVITY})
        destination = self.root / "restored"
        restore(self.archive, destination, apply=True)
        with closing(sqlite3.connect(destination / self.memory)) as restored:
            self.assertEqual(restored.execute("SELECT id FROM documents ORDER BY id").fetchall(), [(1,), (2,)])
        with self.assertRaises(FileExistsError):
            restore(self.archive, destination, apply=True)

    def test_explicit_private_archive_keeps_core_credentials_out(self):
        manifest = self.create(include_private_chat=True, writers_stopped=True)
        self.assertEqual({row["path"] for row in manifest["files"]}, {self.memory, ACTIVITY, *CHAT})
        self.assertEqual(preview_migration("selective", self.archive, selection=["lm-doc-1"])["records"],
                         [{"id": "lm-doc-1", "parent": None}])
        self.assertTrue(preview_migration("full", self.archive)["private_chat"])
        with ZipFile(self.archive, "a") as archive:
            archive.writestr("data/data_v4.db", "extra")
        with self.assertRaises(ValueError):
            verify(self.archive)

    def test_failed_room_sync_keeps_previous_snapshot_and_timestamp(self):
        destination = self.root / "room.json"
        bundle = {"format": "fishhome-room-v1", "connection": "ready", "captured_at": "2026-11-09T06:00:00Z",
                  "memory": {"schema": "memory-observatory/v1"}, "activity": {"schema": "dafeiyu-room/v1"}}
        sync_room(destination, lambda: bundle)

        def disconnected():
            raise OSError("source offline")

        with self.assertRaises(OSError):
            sync_room(destination, disconnected)
        stale = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(stale["connection"], "stale")
        self.assertEqual(stale["captured_at"], bundle["captured_at"])
        self.assertEqual(stale["memory"], bundle["memory"])
