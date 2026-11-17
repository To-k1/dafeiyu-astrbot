import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from deploy.desktop_manager import thinkbook


class ThinkBookConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_routes_actions_and_keeps_manager_token_off_the_browser(self):
        requests = []

        async def manager(request):
            requests.append((request.method, request.path, request.host, request.headers.get("Authorization")))
            return web.json_response({"url": "http://127.0.0.1:6185/"})

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", manager)
        async with TestServer(upstream) as target, ClientSession() as upstream_client:
            with patch.object(thinkbook, "MANAGER_FORWARD", target.port), patch.object(thinkbook, "qq_control_ready") as qq:
                qq.return_value = False
                relay = thinkbook.create_relay(upstream_client, "desktop-browser", "thinkbook-manager")
                async with TestClient(TestServer(relay)) as browser:
                    host = {"Host": "127.0.0.1:16196"}
                    auth = {**host, "Authorization": "Bearer desktop-browser"}
                    page = await browser.get("/", headers=host)
                    html = await page.text()
                    self.assertIn("ThinkBook 远程控制", html)
                    self.assertIn("自主生活与主动联系设置", html)
                    self.assertIn("/委托 列表", html)
                    self.assertIn("OpenCode API Key", html)
                    self.assertIn("http://127.0.0.1:16185/", html)
                    self.assertIn("http://127.0.0.1:18767/room", html)
                    self.assertIn('href="http://127.0.0.1:18767/"', html)
                    self.assertNotIn("thinkbook-manager", html)
                    self.assertEqual((await browser.get("/api/status", headers=host)).status, 401)
                    self.assertEqual((await browser.post("/api/services/napcat/start", headers=auth)).status, 409)
                    self.assertEqual((await browser.post("/api/services/all/start", headers=auth)).status, 409)
                    self.assertEqual((await browser.post("/api/exit", headers=auth)).status, 404)
                    reply = await browser.post("/api/services/ollama/restart", headers=auth)
                    self.assertEqual(await reply.json(), {"url": "http://127.0.0.1:16185/"})
                    qq.return_value = True
                    self.assertEqual((await browser.post("/api/services/napcat/restart", headers=auth)).status, 200)
                    self.assertEqual((await browser.post("/api/services/all/start", headers=auth)).status, 200)
        self.assertEqual(requests, [("POST", path, "127.0.0.1:6196", "Bearer thinkbook-manager") for path in (
            "/api/services/ollama/restart", "/api/services/napcat/restart", "/api/services/all/start")])

    async def test_remote_console_saves_activity_on_thinkbook(self):
        async def configure(request):
            self.assertEqual(request.headers["Authorization"], "Bearer thinkbook-manager")
            self.assertEqual(await request.json(), {"contact_daily_limit": 5})
            return web.json_response({"message": "已保存"})

        upstream = web.Application()
        upstream.router.add_post("/api/config", configure)
        async with TestServer(upstream) as target, ClientSession() as upstream_client:
            with patch.object(thinkbook, "MANAGER_FORWARD", target.port):
                relay = thinkbook.create_relay(upstream_client, "desktop-browser", "thinkbook-manager")
                async with TestClient(TestServer(relay)) as browser:
                    headers = {"Host": "127.0.0.1:16196", "Authorization": "Bearer desktop-browser"}
                    response = await browser.post("/api/config", headers=headers, json={"contact_daily_limit": 5})
                    self.assertEqual(await response.json(), {"message": "已保存"})

    async def test_observatory_proxy_forwards_reads_and_queries_only(self):
        requests = []

        async def observatory(request):
            requests.append((request.method, request.path, request.host))
            return web.Response(body=b"fish", headers={"Content-Type": "image/png", "X-Content-Type-Options": "nosniff"})

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", observatory)
        async with TestServer(upstream) as target, ClientSession() as client:
            with patch.object(thinkbook, "OBSERVATORY_FORWARD", target.port):
                async with TestClient(TestServer(thinkbook.create_observatory_relay(client))) as browser:
                    host = {"Host": "127.0.0.1:18767"}
                    image = await browser.get("/character/idle.png", headers=host)
                    self.assertEqual(await image.read(), b"fish")
                    self.assertEqual(image.content_type, "image/png")
                    self.assertEqual(image.headers["X-Content-Type-Options"], "nosniff")
                    headers = {**host, "Origin": "http://127.0.0.1:18767", "X-Observatory": "local"}
                    self.assertEqual((await browser.post("/api/project", headers=headers, json={})).status, 200)
                    self.assertEqual((await browser.post("/api/import", headers=headers, json={})).status, 404)
                    self.assertEqual((await browser.get("/api/status", headers=host)).status, 404)
                    self.assertEqual((await browser.get("/api/snapshot", headers={**host, "Origin": "https://example.invalid"})).status, 403)
        self.assertEqual(requests, [("GET", "/character/idle.png", "127.0.0.1:8767"),
                                    ("POST", "/api/project", "127.0.0.1:8767")])
