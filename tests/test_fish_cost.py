"""Exercise the request boundary with a local upstream and a controllable clock."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from deploy.fish_cost import OUTPUT_LIMIT, PRICE_FILE, create_app, load_prices, stream_usage
from deploy.fish_cost_ledger import CostLimit, Ledger, amount
from plugin.astrbot_plugin_bili_activity.cost_window import peak_end


def beijing(at):
    return datetime.fromisoformat(at + "+08:00").timestamp()


class LedgerTests(unittest.TestCase):
    def test_budget_day_changes_at_midnight_and_settlement_stays_on_original_day(self):
        clock = [beijing("2026-11-08T23:59:59")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "activity.sqlite3"
            ledger = Ledger(path, lambda: clock[0])
            ledger.adjust("autonomous", "4.25", temporary=False)
            ledger.adjust("autonomous", "1", temporary=True)
            previous = ledger.reserve("autonomous", "anthropic", amount("1"), clock[0])
            ledger.adjust("interaction", "0", temporary=True)
            with self.assertRaises(CostLimit):
                ledger.reserve("autonomous", "anthropic", amount("0.01"), clock[0])
            with self.assertRaises(CostLimit):
                ledger.reserve("interaction", "openai", amount("0.01"), clock[0])
            self.assertEqual(ledger.snapshot()["day"], "2026-11-08")
            clock[0] += 1
            ledger = Ledger(path, lambda: clock[0])
            ledger.settle(previous, amount("0.10"))
            ledger.settle(previous, amount("0.30"))
            self.assertEqual(ledger.snapshot()["day"], "2026-11-09")
            self.assertEqual(ledger.snapshot()["ledgers"]["autonomous"]["remaining"], "4.25")
            clock[0] += 1
            self.assertEqual(ledger.snapshot()["ledgers"]["interaction"]["remaining"], "5")
            clock[0] -= 2
            self.assertEqual(ledger.snapshot()["ledgers"]["autonomous"]["used"], "0.1")
            self.assertEqual(ledger.snapshot()["ledgers"]["interaction"]["remaining"], "0")

    def test_peak_cutoffs_and_holidays(self):
        self.assertEqual(peak_end(beijing("2026-11-09T10:00:00")), beijing("2026-11-09T12:00:00"))
        self.assertIsNone(peak_end(beijing("2026-11-09T12:00:00")))
        self.assertEqual(peak_end(beijing("2026-11-09T14:00:00")), beijing("2026-11-09T18:00:00"))
        self.assertIsNone(peak_end(beijing("2026-10-01T10:00:00")))
        self.assertIsNone(peak_end(beijing("2026-11-08T10:00:00")))


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_dsh_keeps_usage_only_after_upstream_completion(self):
        usage = {"input_tokens": 10, "output_tokens": 4}

        async def upstream_lines(complete):
            yield b'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n'
            yield b'data: {"type":"message_delta","usage":{"output_tokens":4}}\n'
            if complete:
                yield b'data: {"type":"message_stop"}\n'

        for complete in (True, False):
            with self.subTest(complete=complete):
                response = SimpleNamespace(prepare=AsyncMock(), write_eof=AsyncMock(),
                                           write=AsyncMock(side_effect=ConnectionResetError))
                with patch("deploy.fish_cost.web.StreamResponse", return_value=response):
                    _, captured = await stream_usage(object(), SimpleNamespace(content=upstream_lines(complete)),
                                                     "anthropic")
                self.assertEqual(captured, usage if complete else None)
                response.write.assert_awaited_once()
                response.write_eof.assert_not_awaited()

    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.clock = beijing("2026-11-09T10:00:00")
        self.ledger = Ledger(Path(folder.name) / "activity.sqlite3", lambda: self.clock)
        self.calls = []
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        upstream = web.Application()
        upstream.router.add_post("/{path:.*}", self.upstream)
        fake = TestServer(upstream)
        await fake.start_server()
        self.addAsyncCleanup(fake.close)
        self.keys = {"interaction": "interactive-test-credential-1234", "autonomous": "autonomous-test-credential-1234"}
        app = create_app(self.ledger, load_prices(PRICE_FILE), self.keys, "fake-upstream-only",
                         upstream_base=str(fake.make_url("/")).rstrip("/"))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def upstream(self, request):
        body = await request.json()
        self.calls.append((request.path, body, request.headers.get("Authorization"), request.headers.get("x-api-key")))
        if request.path.endswith("/search"):
            return web.json_response({"results": [{}] if body["query"] == "match" else []})
        if request.path.endswith("/fetch"):
            return web.json_response({"content": "sample"})
        if request.path.endswith("/messages"):
            values = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 1}}},
                {"type": "message_delta", "usage": {"output_tokens": 4}},
                {"type": "message_stop"},
            ]
            body = "".join("data: " + json.dumps(value) + "\n\n" for value in values)
            return web.Response(text=body, content_type="text/event-stream")
        if body["messages"][0]["content"] == "wait":
            self.entered.set()
            await self.release.wait()
        if body["messages"][0]["content"] == "unknown":
            return web.json_response({"choices": []})
        if body.get("stream"):
            return web.Response(text='data: {"usage":{"prompt_tokens":14,"prompt_cache_hit_tokens":10,"completion_tokens":2}}\n\ndata: [DONE]\n\n',
                                content_type="text/event-stream")
        return web.json_response({"usage": {"prompt_tokens": 14, "prompt_cache_hit_tokens": 10, "completion_tokens": 2}})

    async def post(self, path, body, bucket="interaction"):
        return await self.client.post(path, json=body, headers={"Authorization": f"Bearer {self.keys[bucket]}"})

    async def test_both_streams_and_tools_settle_by_usage(self):
        model = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "test"}], "stream": True}
        openai = await self.post("/openai/interaction/v1/chat/completions", model)
        self.assertIn("[DONE]", await openai.text())
        self.assertTrue(self.calls[-1][1]["stream_options"]["include_usage"])
        self.assertEqual(self.calls[-1][2], "Bearer fake-upstream-only")
        self.clock = beijing("2026-11-09T12:15:00")
        anthropic = await self.post("/anthropic/autonomous/v1/messages", model | {"max_tokens": 40000}, "autonomous")
        self.assertIn("message_stop", await anthropic.text())
        self.assertEqual(self.calls[-1][1]["max_tokens"], OUTPUT_LIMIT)
        self.assertEqual(self.calls[-1][3], "fake-upstream-only")
        for query in ("match", "empty"):
            search = await self.post("/tools/autonomous/search", {"query": query}, "autonomous")
            self.assertEqual(search.status, 200)
        fetch = await self.post("/tools/autonomous/fetch", {"url": "https://example.org"}, "autonomous")
        self.assertEqual(fetch.status, 200)
        snapshot = self.ledger.snapshot()["ledgers"]
        self.assertEqual(snapshot["interaction"]["used"], "0.0000244")
        self.assertEqual(snapshot["autonomous"]["used"], "0.010028")

    async def test_reservation_blocks_a_concurrent_request_and_unknown_usage_is_retained(self):
        path = "/openai/interaction/v1/chat/completions"
        self.ledger.adjust("interaction", "2.5", temporary=False)
        model = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "wait"}]}
        first = asyncio.create_task(self.post(path, model))
        try:
            await asyncio.wait_for(self.entered.wait(), 5)
            blocked = await self.post(path, model)
            self.assertEqual(blocked.status, 402)
            self.assertEqual((await blocked.json())["error"]["code"], "fish_cost_limit")
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            self.assertEqual((await first).status, 200)
        unknown = model | {"messages": [{"role": "user", "content": "unknown"}]}
        self.assertEqual((await self.post(path, unknown)).status, 200)
        self.assertEqual(self.ledger.snapshot()["ledgers"]["interaction"]["reserved"], "2.162688")
        self.assertEqual((await self.post(path, model)).status, 402)
        self.assertEqual((await self.post(path, model, "autonomous")).status, 401)
        self.assertEqual((await self.post(path, model | {"model": "deepseek-v4-pro"})).status, 400)
        self.assertEqual((await self.post(path, model | {"tools": [{"type": "web_search"}]})).status, 400)
        self.assertEqual(len(self.calls), 2)

    async def test_offline_setup_serves_balance_without_forwarding_or_reserving(self):
        app = create_app(self.ledger, load_prices(PRICE_FILE), self.keys, None, offline=True)
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": f"Bearer {self.keys['interaction']}"}
            reply = await client.get("/balance", headers=headers)
            self.assertEqual(reply.status, 200)
            self.assertEqual((await reply.json())["ledgers"]["interaction"]["remaining"], "5")
            rejected = await client.post("/openai/interaction/v1/chat/completions", json={}, headers=headers)
            self.assertEqual(rejected.status, 503)
            self.assertEqual(self.ledger.snapshot()["ledgers"]["interaction"]["reserved"], "0")
