import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from deploy.desktop_manager import owner_work, thinkbook
from deploy.desktop_manager.public_jobs import PublicJobs

if os.name == "nt":
    from deploy.desktop_manager.manager import Manager, create_app


WEB = {"kind": "web", "query": "查公开文档并提供来源"}
VIDEO = {"kind": "video", "reference": "BV1zUWy6DEEX", "question": "核对前几秒", "asr_mode": "preview"}
DESKTOP = {"kind": "desktop", "instruction": "查询当前可用内存"}
PAGE = {"kind": "page_text", "title": "Official docs", "url": "https://example.org/page",
        "requested_url": "https://example.org/page", "fetched_at": "2026-12-05T00:00:00Z",
        "text": "Actual archived page"}
CONSOLE = {"Host": "127.0.0.1:6196", "Authorization": "Bearer console-test"}
FULL_REPORT = "实际查证过程。\n" * 600 + "\n报告尾行"


@unittest.skipUnless(os.name == "nt", "Windows desktop manager")
class OwnerWorkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.manager = Manager(root / "dev", root / "manager")
        self.manager.options.update(dsh_video_enabled=True, dsh_web_search_enabled=True, dsh_desktop_enabled=True)
        self.manager.creds.values.update(opencode_key="test-key", desktop_token="test-desktop")
        self.manager.runner_password = Mock(return_value="test-runner")
        self.manager.dsh_launch_token = Mock(return_value="test-launch")
        self.manager.matching_pid = Mock(return_value=True)
        self.manager.listeners = Mock(return_value={59934: [42]})
        worker = self.manager.video_worker
        worker.dsh.mkdir(parents=True)
        (worker.dsh / "Start-DSH-Video.cmd").touch()
        self.manager.dsh_asr_options = Mock(return_value={})

        async def search(_password, _go_key, _query, *, archive_page, background_started, owner_report, **_options):
            self.assertTrue(owner_report)
            background_started("waiting_for_result")
            artifact = archive_page(PAGE)
            return {"kind": "dsh_web_search", "summary": "网页简报", "full_report": FULL_REPORT, "evidence": [
                {"kind": "page_text", "url": PAGE["url"], "artifact": artifact}]}

        worker.search = AsyncMock(side_effect=search)
        worker.run = AsyncMock(return_value={"kind": "dsh_video", "summary": "视频报告",
                                            "evidence": [{"source": "https://www.bilibili.com/video/BV1zUWy6DEEX",
                                                          "frame_times": [0, 5]}]})
        worker.desktop = AsyncMock(return_value={"kind": "dsh_windows", "summary": "桌面报告",
                                                 "evidence": [{"action": "memory", "total_bytes": 1024}]})

    async def result(self, client, ident, headers, cookies):
        async with asyncio.timeout(5):
            while True:
                response = await client.get(owner_work.API, params={"id": ident}, headers=headers, cookies=cookies)
                self.assertEqual(response.status, 200)
                reply = await response.json()
                if reply["state"] == "completed":
                    return reply
                self.assertIn(reply["state"], {"queued", "running"})
                await asyncio.sleep(.02)

    async def test_owner_launch_cookie_queues_three_kinds_and_keeps_materials_private(self):
        async with TestClient(TestServer(create_app(self.manager, "console-test"))) as browser:
            host = {"Host": "127.0.0.1:6196"}
            grant = await browser.post("/api/ui/dsh", headers=CONSOLE)
            self.assertEqual(grant.status, 200)
            cookie = "fishhome-owner-work-6196"
            cookies = {cookie: grant.cookies[cookie].value}
            browser.session.cookie_jar.clear()
            page = await browser.get(owner_work.PAGE, headers=host)
            self.assertIn('frame-ancestors http://127.0.0.1:59934', page.headers["Content-Security-Policy"])
            self.assertEqual((await browser.get(owner_work.API, headers=host)).status, 401)
            meeting = "fishhome-meeting-6196"
            self.assertEqual((await browser.get(owner_work.API, headers=host,
                                                 cookies={meeting: grant.cookies[meeting].value})).status, 401)
            self.assertEqual((await browser.get("/api/status", headers=host, cookies=cookies)).status, 401)
            origin = {**host, "Origin": "http://127.0.0.1:6196"}
            foreign = {**host, "Origin": "http://127.0.0.1:59934"}
            ids = ["a" * 32, "b" * 32, "c" * 32]
            self.assertEqual((await browser.put(owner_work.API, params={"id": ids[0]}, headers=foreign,
                                                 cookies=cookies, json=WEB)).status, 401)
            for ident, task in zip(ids, (WEB, VIDEO, DESKTOP)):
                response = await browser.put(owner_work.API, params={"id": ident}, headers=origin,
                                             cookies=cookies, json=task)
                self.assertEqual(response.status, 200)
                self.assertIn((await response.json())["state"], {"queued", "running"})
            report = await self.result(browser, ids[0], host, cookies)
            self.assertEqual(report["report"]["summary"], "网页简报")
            self.assertTrue(report["report"]["has_full_report"])
            self.assertNotIn("full_report", report["report"])
            artifact = report["report"]["evidence"][0]["artifact"]
            self.assertTrue((self.manager.owner_work / artifact).is_file())
            download = await browser.get(owner_work.API, params={"artifact": artifact}, headers=host, cookies=cookies)
            self.assertIn("attachment", download.headers["Content-Disposition"])
            self.assertIn(PAGE["text"], await download.text())
            legacy = await self.result(browser, ids[1], host, cookies)
            self.assertEqual(legacy["report"]["summary"], "视频报告")
            self.assertFalse(legacy["report"]["has_full_report"])
            self.assertEqual((await browser.get(owner_work.API, params={"id": ids[1], "report": "view"},
                                                headers=host, cookies=cookies)).status, 404)
            await self.result(browser, ids[2], host, cookies)
            duplicate = await browser.put(owner_work.API, params={"id": ids[0]}, headers=origin,
                                          cookies=cookies, json=WEB)
            self.assertEqual((await duplicate.json())["state"], "completed")
            listing = await (await browser.get(owner_work.API, headers=host, cookies=cookies)).json()
            self.assertEqual({row["task"]["kind"] for row in listing["jobs"]}, {"web", "video", "desktop"})
        for worker in (self.manager.video_worker.search, self.manager.video_worker.run, self.manager.video_worker.desktop):
            worker.assert_awaited_once()
        self.assertTrue(self.manager.video_worker.search.await_args.kwargs["owner_report"])
        self.assertTrue(self.manager.video_worker.desktop.await_args.kwargs["owner_report"])
        self.assertEqual(self.manager.video_worker.run.await_args.kwargs["asr_mode"], "preview")
        self.assertNotIn("asr_key", self.manager.video_worker.run.await_args.kwargs)
        self.assertTrue(all(self.manager.public_jobs.get(ident)["state"] == "not_found" for ident in ids))
        self.assertFalse(self.manager.ledger.exists())

    async def test_remote_relay_reads_same_owner_job_and_downloads_the_private_archive(self):
        async with TestServer(create_app(self.manager, "console-test")) as manager, ClientSession() as upstream:
            with patch.object(thinkbook, "MANAGER_FORWARD", manager.port):
                app = thinkbook.create_relay(upstream, "browser-test", "console-test")
                async with TestClient(TestServer(app)) as browser:
                    host = {"Host": "127.0.0.1:16196"}
                    auth = {**host, "Authorization": "Bearer browser-test"}
                    response = await browser.post("/api/ui/dsh", headers=auth)
                    self.assertEqual(response.status, 200)
                    cookie = "fishhome-owner-work-16196"
                    cookies = {cookie: response.cookies[cookie].value}
                    browser.session.cookie_jar.clear()
                    ident = "d" * 32
                    origin = {**host, "Origin": "http://127.0.0.1:16196"}
                    accepted = await browser.put(owner_work.API, params={"id": ident}, headers=origin,
                                                 cookies=cookies, json=WEB)
                    self.assertEqual(accepted.status, 200)
                    report = await self.result(browser, ident, host, cookies)
                    reopened = PublicJobs(self.manager.owner_work / "jobs", execute=None)
                    self.assertEqual(reopened.get(ident)["report"]["full_report"], FULL_REPORT)
                    view_params = {"id": ident, "report": "view"}
                    denied = await browser.get(owner_work.API, params=view_params, headers=host)
                    self.assertEqual(denied.status, 401)
                    viewed = await browser.get(owner_work.API, params=view_params, headers=host, cookies=cookies)
                    self.assertEqual((await viewed.json())["text"], FULL_REPORT)
                    downloaded = await browser.get(owner_work.API, params={"id": ident, "report": "download"},
                                                   headers=host, cookies=cookies)
                    self.assertIn("attachment", downloaded.headers["Content-Disposition"])
                    self.assertEqual(await downloaded.text(), FULL_REPORT)
                    artifact = report["report"]["evidence"][0]["artifact"]
                    archived = await browser.get(owner_work.API, params={"artifact": artifact},
                                                 headers=host, cookies=cookies)
                    self.assertIn(PAGE["text"], await archived.text())
                    self.assertIn("attachment", archived.headers["Content-Disposition"])
                    invalid_path = await browser.get(owner_work.API, params={"artifact": "../../credentials.bin"},
                                                      headers=host, cookies=cookies)
                    self.assertEqual(invalid_path.status, 409)
