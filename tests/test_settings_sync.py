import json
import os
import tempfile
import unittest
from pathlib import Path

if os.name == "nt":
    from deploy.desktop_manager.settings_sync import SettingsSync, SharedSettings
    from deploy.desktop_manager.windows import write_json
    from deploy.fish_cost_ledger import Ledger


class Peer:
    def __init__(self, store):
        self.store = store
        self.offline = False

    async def request(self, body=None):
        if self.offline:
            raise OSError("offline")
        return self.store.apply(**body) if body else self.store.snapshot()


@unittest.skipUnless(os.name == "nt", "Windows desktop manager")
class SettingsSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.desktop = self.make_host(root / "desktop", 3)
        self.thinkbook = self.make_host(root / "thinkbook", 6)
        self.peer = Peer(self.thinkbook)
        self.baseline = root / "baseline.json"
        self.sync = SettingsSync(self.desktop, self.baseline, self.peer)

    def make_host(self, path, limit):
        path.mkdir()
        control = path / "control.json"
        write_json(control, {"contact_daily_limit": limit, "periodic_enabled": False, "owner_id": "local-only"})
        ledger = path / "activity.sqlite3"
        Ledger(ledger)
        return SharedSettings(control, ledger)

    @staticmethod
    def choose(view, side="thinkbook"):
        return {"expected": {row["key"]: {"desktop": row["desktop"], "thinkbook": row["thinkbook"]} for row in view["rows"]},
                "choices": {row["key"]: side for row in view["rows"]}}

    def change(self, store, **changes):
        before = store.snapshot()
        return store.apply({key: before[key] for key in changes}, changes)

    async def test_initial_preview_does_not_write_and_rejects_stale_confirmation(self):
        preview = await self.sync.check()
        self.assertEqual(preview["rows"], [{"key": "contact_daily_limit", "desktop": 3, "thinkbook": 6}])
        self.assertFalse(self.baseline.exists())
        self.change(self.thinkbook, contact_daily_limit=7)
        stale = await self.sync.check(confirmation=self.choose(preview))
        self.assertIn("重新确认", stale["error"])
        self.assertEqual(self.desktop.snapshot()["contact_daily_limit"], 3)
        result = await self.sync.check(confirmation=self.choose(stale))
        self.assertTrue(result["initialized"])
        self.assertEqual(self.desktop.snapshot()["contact_daily_limit"], 7)
        self.assertEqual(json.loads(self.baseline.read_text(encoding="utf-8")), self.thinkbook.snapshot())

    async def test_offline_edits_merge_by_field_and_same_field_conflicts_are_preserved(self):
        await self.sync.check(confirmation=self.choose(await self.sync.check()))
        self.peer.offline = True
        self.change(self.desktop, contact_daily_limit=8, daily_minutes=36)
        self.change(self.thinkbook, contact_daily_limit=10, contact_cooldown_minutes=75)
        self.assertTrue((await self.sync.check())["error"])
        self.peer.offline = False
        restarted = SettingsSync(self.desktop, self.baseline, self.peer)
        result = await restarted.check()
        self.assertEqual(result["rows"], [{"key": "contact_daily_limit", "desktop": 8, "thinkbook": 10}])
        self.assertEqual(self.desktop.snapshot()["contact_cooldown_minutes"], 75)
        self.assertEqual(self.thinkbook.snapshot()["daily_minutes"], 36)
        result = await restarted.check(confirmation=self.choose(result, "desktop"))
        self.assertEqual(result["rows"], [])
        self.assertEqual(self.thinkbook.snapshot()["contact_daily_limit"], 8)
        self.assertEqual(json.loads(self.thinkbook.control.read_text(encoding="utf-8"))["owner_id"], "local-only")

    async def test_only_defaults_cross_machines_and_active_day_override_is_kept(self):
        await self.sync.check(confirmation=self.choose(await self.sync.check()))
        desktop = Ledger(self.desktop.ledger)
        thinkbook = Ledger(self.thinkbook.ledger)
        thinkbook.adjust("interaction", "2", temporary=True)
        desktop.adjust("interaction", "7.25", temporary=False)
        await self.sync.check()
        self.assertEqual(self.thinkbook.snapshot()["default_interaction_budget"], "7.25")
        self.assertEqual(thinkbook.snapshot()["ledgers"]["interaction"]["limit"], "2")
        self.assertEqual(desktop.snapshot()["ledgers"]["interaction"]["limit"], "7.25")
