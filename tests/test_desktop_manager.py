import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
from aiohttp.test_utils import TestClient, TestServer

if os.name == "nt":
    from deploy.desktop_manager.manager import Manager, create_app, qq_owner, read_settings
    from deploy.desktop_manager.windows import Credentials, write_json
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

    async def test_opencode_key_is_saved_encrypted_and_only_reports_presence(self):
        value = "oc-test-placeholder-for-storage"
        denied = await self.client.post("/api/config", headers={"Host": self.auth["Host"]}, json={"opencode_key": value})
        self.assertEqual(denied.status, 401)
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertFalse((await result.json())["opencode_key_saved"])
        saved = await self.client.post("/api/config", headers=self.auth, json={"opencode_key": value})
        self.assertEqual(saved.status, 200)
        self.assertNotIn(value, await saved.text())
        credentials = Credentials(self.manager.creds.path)
        self.assertEqual(credentials.values["opencode_key"], value)
        self.assertNotIn(value.encode(), credentials.path.read_bytes())
        self.assertNotIn("upstream_key", credentials.values)
        for url in ("/api/config", "/api/provider-keys"):
            async with self.client.get(url, headers=self.auth) as result:
                payload = await result.json()
                self.assertNotIn(value, str(payload))
                if url == "/api/config":
                    self.assertTrue(payload["opencode_key_saved"])

    async def test_desktop_password_is_encrypted_and_never_returned(self):
        value = "desktop-password-fixture"
        denied = await self.client.post("/api/config", headers={"Host": self.auth["Host"]},
                                        json={"desktop_password": value})
        self.assertEqual(denied.status, 401)
        with patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()) as logon:
            saved = await self.client.post("/api/config", headers=self.auth, json={"desktop_password": value})
        self.assertEqual(saved.status, 200)
        self.assertNotIn(value, await saved.text())
        logon.assert_called_once_with("FishHomeDesktop", value)
        credentials = Credentials(self.manager.creds.path)
        self.assertEqual(credentials.values["desktop_password"], value)
        self.assertNotIn(value.encode(), credentials.path.read_bytes())
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)):
            async with self.client.get("/api/config", headers=self.auth) as response:
                payload = await response.json()
        self.assertTrue(payload["desktop_password_saved"])
        self.assertNotIn(value, str(payload))

    async def test_desktop_auto_requires_password_and_controls_all_start(self):
        self.assertNotIn("desktop", self.manager.specs())
        response = await self.client.post("/api/config", headers=self.auth, json={"desktop_auto_enabled": True})
        self.assertEqual(response.status, 409)
        self.assertFalse(self.manager.options.get("desktop_auto_enabled", False))
        self.manager.creds.values["desktop_password"] = "fixture"
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name) / "profile"):
            service = self.manager.specs()["desktop"]
            self.assertEqual(service.account, "FishHomeDesktop")
            with patch.object(self.manager, "action", new_callable=AsyncMock, return_value="started") as action:
                response = await self.client.post("/api/services/all/start", headers=self.auth)
                self.assertEqual(response.status, 200)
                self.assertNotIn("desktop", (await response.json())["results"])
                action.assert_any_await("budget", "start")
                action.reset_mock()
                response = await self.client.post("/api/config", headers=self.auth, json={"desktop_auto_enabled": True})
                self.assertEqual(response.status, 200)
                response = await self.client.post("/api/services/all/start", headers=self.auth)
                self.assertEqual((await response.json())["results"]["desktop"], "started")
                action.assert_any_await("desktop", "start")

    async def test_desktop_running_in_another_session_is_not_reported_as_running(self):
        self.manager.creds.values["desktop_password"] = "fixture"
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=True), \
             patch("deploy.desktop_manager.desktop_session.get_session", return_value={"session_id": 1}), \
             patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()) as impersonate, \
             patch("deploy.desktop_manager.desktop_session.session_id", return_value=2) as session_id:
            with self.assertRaisesRegex(ValueError, "另一 Windows 会话"):
                await self.manager.start("desktop")
            session_id.assert_called_once_with(123)
            impersonate.assert_called_once_with("FishHomeDesktop", "fixture")
            session_id.return_value = 1
            self.assertEqual(await self.manager.start("desktop"), "已在运行")

    async def test_desktop_auto_start_is_locked_and_failure_does_not_break_console(self):
        self.manager.creds.values["desktop_password"] = "fixture"
        self.manager.options["desktop_auto_enabled"] = True
        async def fail_start(name):
            self.assertEqual(name, "desktop")
            self.assertTrue(self.manager.lock.locked())
            raise RuntimeError("private error")
        with patch.object(self.manager, "start", side_effect=fail_start) as start:
            client = TestClient(TestServer(create_app(self.manager, "other-token")))
            await client.start_server()
            try:
                start.assert_awaited_once_with("desktop")
                response = await client.get("/api/ping", headers={"Host": "127.0.0.1:6196", "Authorization": "Bearer other-token"})
                self.assertEqual(response.status, 200)
            finally:
                await client.close()
        log = (self.manager.state / "manager.log").read_text(encoding="utf-8")
        self.assertIn("桌面服务自动启动失败：RuntimeError", log)
        self.assertNotIn("private error", log)

    async def test_desktop_auto_start_requires_both_settings(self):
        self.manager.options["desktop_auto_enabled"] = True
        with patch.object(self.manager, "start", new_callable=AsyncMock) as start:
            client = TestClient(TestServer(create_app(self.manager, "other-token")))
            await client.start_server()
            await client.close()
            start.assert_not_awaited()

    async def test_desktop_stop_refusal_preserves_tracking(self):
        self.manager.creds.values.update(desktop_password="fixture", desktop_token="token-fixture")
        self.manager.launches["desktop"] = {"pid": 123, "time": 456}
        write_json(self.manager.launch_file, self.manager.launches)
        response = MagicMock(status=409)
        response.json = AsyncMock(return_value={"error": "任务正在运行"})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.post.return_value = request
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=session)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=123), \
             patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()), \
             patch("deploy.desktop_manager.manager.psutil.Process") as process, \
             patch("deploy.desktop_manager.manager.ClientSession", return_value=client):
            process.return_value.create_time.return_value = 456
            with self.assertRaisesRegex(ValueError, "任务正在运行"):
                await self.manager.stop("desktop")
        session.post.assert_called_once_with("http://127.0.0.1:59937/desktop", json={"op": "shutdown"},
                                             headers={"Authorization": "Bearer token-fixture"})
        self.assertEqual(self.manager.launches["desktop"], {"pid": 123, "time": 456})
        self.assertEqual(read_settings(self.manager.launch_file)["desktop"], {"pid": 123, "time": 456})

    async def test_desktop_shutdown_waits_for_exit_and_retains_tracking_on_timeout(self):
        self.manager.creds.values.update(desktop_password="fixture", desktop_token="token-fixture")
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"ok": True})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.post.return_value = request
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=session)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=123), \
             patch("deploy.desktop_manager.manager.impersonate", side_effect=lambda *args: nullcontext()) as login, \
             patch("deploy.desktop_manager.manager.psutil.Process") as process, \
             patch("deploy.desktop_manager.manager.ClientSession", return_value=client):
            process.return_value.create_time.return_value = 456
            self.manager.launches["desktop"] = {"pid": 123, "time": 456}
            write_json(self.manager.launch_file, self.manager.launches)
            process.return_value.wait.side_effect = psutil.TimeoutExpired(8, 123)
            with self.assertRaisesRegex(ValueError, "尚未退出"):
                await self.manager.stop("desktop")
            self.assertIn("desktop", self.manager.launches)
            self.assertIn("desktop", read_settings(self.manager.launch_file))
            process.return_value.wait.assert_called_with(timeout=8)
            self.assertGreaterEqual(login.call_count, 2)
            process.return_value.wait.side_effect = None
            result = await self.manager.stop("desktop")
            self.assertEqual(result, "桌面服务已停止")
            self.assertNotIn("desktop", self.manager.launches)
            self.assertNotIn("desktop", read_settings(self.manager.launch_file))

    async def test_public_video_has_separate_credentials_and_switch(self):
        path = "/api/worker/public-video"
        worker = AsyncMock(return_value={"kind": "dsh_video", "summary": "sample"})
        self.manager.video_worker.run = worker
        self.manager.video_worker.dsh.mkdir(parents=True)
        (self.manager.video_worker.dsh / "Start-DSH-Video.cmd").touch()
        self.manager.creds.values.update(opencode_key="go-key-fixture", runner_password="runner-fixture")
        body = {"reference": "BV1g8Wq6DEXj", "question": "核对开头"}
        self.assertEqual((await self.client.post(path, headers=self.auth, json=body)).status, 401)
        self.assertEqual((await self.client.post("/api/config", headers=self.auth, json={"dsh_video_enabled": True})).status, 200)
        token = read_settings(self.manager.bridge_settings)["video_worker_token"]
        headers = {**self.auth, "Authorization": "Bearer " + token}
        self.assertEqual((await self.client.post(path, headers=self.auth, json=body)).status, 401)
        self.assertEqual((await self.client.get("/api/config", headers=headers)).status, 401)
        result = await self.client.post(path, headers=headers, json=body)
        self.assertEqual(await result.json(), {"kind": "dsh_video", "summary": "sample"})
        worker.assert_awaited_once_with("runner-fixture", "go-key-fixture", *body.values())
        await self.client.post("/api/config", headers=self.auth, json={"dsh_video_enabled": False})
        self.assertEqual((await self.client.post(path, headers=headers, json=body)).status, 401)

    async def test_activity_settings_read_defaults_and_change_the_runtime_file(self):
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual((await result.json())["activity_settings"]["contact_daily_limit"], 3)
        for invalid in (True, "5", 51):
            reply = await self.client.post("/api/config", headers=self.auth, json={"contact_daily_limit": invalid})
            self.assertEqual(reply.status, 409)
        reply = await self.client.post("/api/config", headers=self.auth, json={"contact_daily_limit": 5})
        self.assertEqual(reply.status, 200)
        self.assertEqual(read_settings(self.manager.control), {"public_interactions": False, "contact_daily_limit": 5})
        self.assertTrue(list((self.manager.activity / "config_backups").glob("control-*.json")))
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual((await result.json())["activity_settings"]["contact_daily_limit"], 5)

    async def test_activity_toggle_preserves_prerequisites_and_source_edits_preserve_credentials(self):
        response = await self.client.post("/api/config", headers=self.auth, json={"periodic_enabled": True})
        self.assertEqual(response.status, 409)
        response = await self.client.post("/api/config", headers=self.auth, json={"commission_sources": ["../secret.txt"]})
        self.assertEqual(response.status, 409)
        response = await self.client.post("/api/config", headers=self.auth, json={"commission_sources": ["sample.csv"]})
        self.assertEqual(response.status, 200)
        async with self.client.get("/api/config", headers=self.auth) as result:
            payload = await result.json()
            self.assertEqual(payload["dsh_sources"]["commission_sources"], ["sample.csv"])
            self.assertNotIn("private-cookie", str(payload))
        self.assertIn("private-cookie", self.manager.bridge_settings.read_text(encoding="utf-8"))

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

    async def test_shared_settings_are_authenticated_and_reject_stale_or_out_of_scope_updates(self):
        Ledger(self.manager.ledger)
        denied = await self.client.get("/api/shared-settings", headers={"Host": self.auth["Host"]})
        self.assertEqual(denied.status, 401)
        async with self.client.get("/api/shared-settings", headers=self.auth) as response:
            defaults = await response.json()
            self.assertEqual(defaults["default_interaction_budget"], "5")
            self.assertNotIn("periodic_enabled", defaults)
            self.assertNotIn("opencode_key", defaults)
        bad = {"expected": {"periodic_enabled": False}, "changes": {"periodic_enabled": True}}
        response = await self.client.post("/api/shared-settings", headers=self.auth, json=bad)
        self.assertEqual(response.status, 409)
        stale = {"expected": {"contact_daily_limit": 99}, "changes": {"contact_daily_limit": 5}}
        response = await self.client.post("/api/shared-settings", headers=self.auth, json=stale)
        self.assertEqual(response.status, 409)
        stale["expected"]["contact_daily_limit"] = 4
        async with self.client.post("/api/shared-settings", headers=self.auth, json=stale) as response:
            self.assertEqual((await response.json())["contact_daily_limit"], 3)
        stale["expected"]["contact_daily_limit"] = 3
        async with self.client.post("/api/shared-settings", headers=self.auth, json=stale) as response:
            self.assertEqual((await response.json())["contact_daily_limit"], 5)

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
