import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

if os.name == "nt":
    from deploy.desktop_manager.manager import Manager, create_app, qq_owner
    from deploy.desktop_manager.windows import write_json
    from deploy.fish_cost_ledger import Ledger
    from scripts.native_archive import LM, verify


@unittest.skipUnless(os.name == "nt", "Windows desktop manager")
class DesktopManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch.dict(os.environ, {"ComSpec": r"C:\Windows\System32\cmd.exe"}):
            self.manager = Manager(root / "dev", root / "state")
        self.manager.bridge_settings.parent.mkdir(parents=True)
        self.manager.activity.mkdir()
        write_json(self.manager.bridge_settings, {"session_cookie": "private-cookie"})
        write_json(self.manager.control, {"public_interactions": False})
        self.scope = "FishQQ:FriendMessage:12345"
        self.config = {
            "admins_id": ["12345", "astrbot"],
            "platform": [{"enable": True, "type": "aiocqhttp", "id": "FishQQ", "ws_reverse_port": 6199}],
            "platform_settings": {"enable_id_white_list": True, "id_whitelist": [self.scope],
                                  "wl_ignore_admin_on_group": False, "wl_ignore_admin_on_friend": False},
        }
        write_json(self.manager.astrbot_settings, self.config)
        self.client = TestClient(TestServer(create_app(self.manager, "test-token")))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.auth = {"Host": "127.0.0.1:6196", "Authorization": "Bearer test-token"}

    async def test_owner_setup_keeps_cookie_and_never_returns_it(self):
        private = await self.client.get("/api/config", headers={"Host": "127.0.0.1:6196"})
        self.assertEqual(private.status, 401)
        async with self.client.post("/api/config", headers=self.auth, json={"owner_qq": "12345"}) as result:
            self.assertEqual(result.status, 200)
        async with self.client.post("/api/config", headers=self.auth, json={"napcat_qq": "23456"}) as result:
            self.assertEqual(result.status, 200)
        self.assertEqual(self.manager.verify_qq()["session_cookie"], "private-cookie")
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual(result.status, 200)
            self.assertNotIn("private-cookie", await result.text())

    def test_extra_admin_cannot_bypass_private_whitelist(self):
        self.assertEqual(qq_owner(self.config, "12345"), self.scope)
        self.config["platform_settings"]["wl_ignore_admin_on_friend"] = True
        with self.assertRaises(ValueError):
            qq_owner(self.config, "12345")

    async def test_budget_update_uses_existing_shared_ledger(self):
        ledger = Ledger(self.manager.ledger)
        budget = {"bucket": "autonomous", "amount": "0", "temporary": True}
        async with self.client.post("/api/budgets", headers=self.auth, json=budget) as result:
            self.assertEqual(result.status, 200)
        async with self.client.get("/api/budgets", headers=self.auth) as result:
            self.assertEqual(result.status, 200)
            self.assertEqual(await result.json(), ledger.snapshot())
            self.assertEqual(ledger.snapshot()["ledgers"]["autonomous"]["limit"], "0")
            self.assertEqual(ledger.snapshot()["ledgers"]["interaction"]["limit"], "5")

    async def test_reject_public_enable_with_old_activity_plugin(self):
        async with self.client.post("/api/config", headers=self.auth, json={"public_write": True}) as result:
            self.assertEqual(result.status, 409)
        self.assertFalse(self.manager.configuration()["public_write"])

    async def test_backup_route_creates_private_safe_archive_and_lists_it(self):
        memory = self.manager.astro / LM / "livingmemory.db"
        memory.parent.mkdir(parents=True)
        with closing(sqlite3.connect(memory)) as db, db:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
        Ledger(self.manager.ledger)
        async with self.client.get("/api/archives", headers={"Host": "127.0.0.1:6196"}) as result:
            self.assertEqual(result.status, 401)
        with patch("deploy.desktop_manager.manager.ROOT", self.manager.dev.parent):
            async with self.client.post("/api/archives/backup", headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                name = (await result.json())["name"]
        manifest = verify(self.manager.backups / name)
        self.assertIsNone(manifest["revision"])
        self.assertEqual(manifest["environment"], "prod")
        self.assertEqual(manifest["consistency"], "sqlite-online")
        self.assertFalse(manifest["include_private_chat"])
        async with self.client.get("/api/archives", headers=self.auth) as result:
            self.assertEqual([row["name"] for row in (await result.json())["backups"]], [name])

    async def test_recovery_route_is_desktop_only_and_lists_package(self):
        from deploy.desktop_manager import manager as console

        route = "/api/archives/recovery"
        async with self.client.post(route, headers=self.auth) as result:
            self.assertEqual(result.status, 409)
        name = "thinkbook-recovery-20261113T092729.770820Z.zip"

        def pull(destination):
            destination.mkdir()
            package = destination / name
            package.touch()
            return package

        with patch.object(console, "DESKTOP_HOME", self.manager.dev.parent), patch.object(console, "capture_and_pull", pull):
            async with self.client.get("/", headers=self.auth) as result:
                self.assertIn('id="recovery">', await result.text())
            async with self.client.post(route, headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                self.assertEqual((await result.json())["name"], name)
        async with self.client.get("/api/archives", headers=self.auth) as result:
            self.assertEqual([row["name"] for row in (await result.json())["backups"]], [name])

    async def test_thinkbook_export_uses_desktop_exports_directory(self):
        from deploy.desktop_manager import manager as console

        route = "/api/archives/export-thinkbook"
        async with self.client.post(route, headers=self.auth) as result:
            self.assertEqual(result.status, 409)
        with (patch.object(console, "DESKTOP_HOME", self.manager.dev.parent),
              patch.object(console, "export_from_thinkbook", side_effect=lambda path: path) as export):
            async with self.client.get("/", headers=self.auth) as result:
                self.assertIn('id="export-thinkbook">', await result.text())
            async with self.client.post(route, headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                self.assertEqual((await result.json())["name"], export.call_args.args[0].name)
        self.assertEqual(export.call_args.args[0].parent, self.manager.exports)
