import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from deploy.dsh_bridge.go_usage import read_go_usage


class GoUsageStatusTests(unittest.IsolatedAsyncioTestCase):
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
