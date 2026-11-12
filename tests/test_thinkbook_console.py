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
            with patch.object(thinkbook, "MANAGER_FORWARD", target.port):
                relay = thinkbook.create_relay(upstream_client, "desktop-browser", "thinkbook-manager")
                async with TestClient(TestServer(relay)) as browser:
                    host = {"Host": "127.0.0.1:16196"}
                    auth = {**host, "Authorization": "Bearer desktop-browser"}
                    page = await browser.get("/", headers=host)
                    html = await page.text()
                    self.assertIn("ThinkBook 远程控制", html)
                    self.assertIn("http://127.0.0.1:16185/", html)
                    self.assertNotIn("thinkbook-manager", html)
                    self.assertEqual((await browser.get("/api/status", headers=host)).status, 401)
                    self.assertEqual((await browser.post("/api/services/napcat/start", headers=auth)).status, 409)
                    self.assertEqual((await browser.post("/api/services/all/start", headers=auth)).status, 409)
                    self.assertEqual((await browser.post("/api/exit", headers=auth)).status, 404)
                    reply = await browser.post("/api/services/ollama/restart", headers=auth)
                    self.assertEqual(await reply.json(), {"url": "http://127.0.0.1:16185/"})
        self.assertEqual(requests, [("POST", "/api/services/ollama/restart", "127.0.0.1:6196",
                                    "Bearer thinkbook-manager")])
