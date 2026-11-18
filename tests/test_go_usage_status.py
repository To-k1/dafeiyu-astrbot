import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from deploy.dsh_bridge.go_usage import read_go_key_usage, read_go_usage


class GoUsageStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_is_read_with_the_configured_go_key(self):
        async def usage(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer fixture-key")
            item = {"percent": 20, "status": "ok", "resetsAt": "2026-11-23T00:00:00.000Z"}
            return web.json_response({"usage": {"rolling": item, "weekly": item,
                                               "monthly": {**item, "percent": 100, "status": "rate-limited"}}})

        app = web.Application()
        app.router.add_get("/usage", usage)
        async with TestServer(app) as server:
            with patch("deploy.dsh_bridge.go_usage.GO_USAGE_URL", str(server.make_url("/usage"))):
                value = await read_go_key_usage("fixture-key")
        self.assertEqual(value["status"], "limited")
        self.assertEqual(value["windows"]["rolling"], {"percent": 20, "limited": False, "resets_at_ms": 1795392000000})
        self.assertTrue(value["windows"]["monthly"]["limited"])

    async def test_pairing_and_official_service_responses(self):
        self.assertIn("尚未配对", (await read_go_usage(28894, ""))["reason"])
        self.assertIn("端口配置无效", (await read_go_usage("28894", "fixture"))["reason"])
        status = 401
        payload = None

        async def usage(request):
            self.assertEqual(request.headers.get("Authorization"), "Basic b3BlbmNvZGU6Zml4dHVyZQ==")
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_get("/api/experimental/usage/limits", usage)
        async with TestServer(app) as server:
            port = server.make_url("/").port
            self.assertIn("认证失败", (await read_go_usage(port, "fixture"))["reason"])
            status = 404
            self.assertIn("接口或工作区不可用", (await read_go_usage(port, "fixture"))["reason"])
            status = 200
            self.assertIn("未取得 Go 额度窗口", (await read_go_usage(port, "fixture"))["reason"])
            item = {"percent": 20, "limited": False, "resetsAt": 1794789000000}
            payload = {"go": {name: item for name in ("rolling", "weekly", "monthly")}}
            self.assertEqual((await read_go_usage(port, "fixture"))["status"], "available")
        self.assertIn("无法连接", (await read_go_usage(port, "fixture"))["reason"])
