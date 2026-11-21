"""Exercise the request boundary with a local upstream and a controllable clock."""

import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from deploy.fish_cost import OUTPUT_LIMIT, PRICE_FILE, create_app, load_prices, normalize_model, stream_usage
from deploy.fish_cost_prompt import IMAGE_TOKEN_LIMIT, openai_input_quote
from plugin.astrbot_plugin_bili_activity.commissioned import CommissionedFiles
from plugin.astrbot_plugin_bili_activity.core import Store


def _vision_request(role, url):
    return {"model": "deepseek-flash", "messages": [{"role": role, "content": [
        {"type": "image_url", "image_url": {"url": url}},
    ]}]}


class VisionGatewayTests(unittest.TestCase):
    def test_quote_counts_text_tools_and_picture_allowance_without_base64(self):
        text = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "鲸鱼🐋"}]}
        tool = {"type": "function", "function": {"name": "lookup", "description": "x" * 5000,
                                                    "parameters": {"type": "object"}}}
        self.assertGreater(openai_input_quote({**text, "tools": [tool]}), openai_input_quote(text) + 5000)
        short_image = _vision_request("user", "data:image/jpeg;base64,YWJj")
        long_image = _vision_request("user", "data:image/jpeg;base64," + "YWJj" * 100_000)
        self.assertEqual(openai_input_quote(short_image), openai_input_quote(long_image))
        self.assertGreaterEqual(openai_input_quote(short_image) - openai_input_quote({**text, "messages": [
            {"role": "user", "content": ""}]}), IMAGE_TOKEN_LIMIT)
        self.assertIsNone(openai_input_quote({**text, "user_context": "unmodeled metadata"}))

    def test_only_inline_user_images_are_accepted_on_openai_route(self):
        inline = "data:image/jpeg;base64,YWJj"
        self.assertEqual(normalize_model(_vision_request("user", inline), "openai"), OUTPUT_LIMIT)
        for role, url, protocol in (("tool", inline, "openai"), ("user", inline, "anthropic"),
                                    ("user", "https://example.com/a.jpg", "openai")):
            with self.assertRaises(ValueError):
                normalize_model(_vision_request(role, url), protocol)
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
        self.upstream_base = str(fake.make_url("/")).rstrip("/")
        self.search_key = "private-manager-search-credential"
        app = create_app(self.ledger, load_prices(PRICE_FILE), self.keys, "fake-upstream-only",
                         upstream_base=self.upstream_base, search_token=self.search_key)
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
        if body["messages"][0]["content"] == "unexpected-count":
            return web.json_response({"usage": {"prompt_tokens": 10_000, "prompt_cache_hit_tokens": 9999,
                                                 "completion_tokens": 2}})
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

    async def test_delegated_search_is_itemized_with_no_daily_cap(self):
        self.ledger.adjust("interaction", "0", temporary=False)
        path = "/tools/web_search/search"
        self.assertEqual((await self.post(path, {"query": "match"})).status, 401)
        self.assertEqual((await self.post("/tools/interaction/search", {"query": "match"})).status, 402)
        headers = {"Authorization": "Bearer " + self.search_key}
        for query in ("match", "empty"):
            reply = await self.client.post(path, json={"query": query}, headers=headers)
            self.assertEqual(reply.status, 200)
        self.assertEqual((await self.client.post("/tools/web_search/fetch", json={}, headers=headers)).status, 401)
        self.assertEqual(len(self.calls), 2)
        report = self.ledger.snapshot()
        self.assertEqual(report["ledgers"]["interaction"]["used"], "0")
        self.assertEqual(report["search"]["web_search"], {"calls": 1, "used": "0.005", "reserved": "0"})

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

    async def test_request_quote_uses_small_balance_atomically_and_holds_missing_usage(self):
        app = create_app(self.ledger, load_prices(PRICE_FILE), self.keys, "fake-upstream-only",
                         upstream_base=self.upstream_base, use_request_bounds=True)
        path = "/openai/interaction/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.keys['interaction']}"}
        model = {"model": "deepseek-flash", "max_tokens": 40, "messages": [{"role": "user", "content": "wait"}]}
        cap = openai_input_quote(model) * 2000 + model["max_tokens"] * 8000
        self.ledger.adjust("interaction", str(cap / 1_000_000_000), temporary=True)
        async with TestClient(TestServer(app)) as client:
            first = asyncio.create_task(client.post(path, json=model, headers=headers))
            try:
                await asyncio.wait_for(self.entered.wait(), 5)
                rejected = await client.post(path, json=model, headers=headers)
                self.assertEqual(rejected.status, 402)
            finally:
                self.release.set()
                self.assertEqual((await first).status, 200)
            self.ledger.adjust("interaction", "0.02", temporary=True)
            unknown = model | {"messages": [{"role": "user", "content": "unknown"}]}
            accepted = await client.post(path, json=unknown, headers=headers)
            self.assertEqual(accepted.status, 200)
            held = self.ledger.snapshot()["ledgers"]["interaction"]["reserved"]
            self.assertEqual(amount(held), openai_input_quote(unknown) * 2000 + 40 * 8000)
            opaque = model | {"user_context": "hidden"}
            self.assertEqual((await client.post(path, json=opaque, headers=headers)).status, 402)
            self.assertEqual(len(self.calls), 2)

    async def test_count_overrun_disables_request_quote_even_with_cache_discount(self):
        app = create_app(self.ledger, load_prices(PRICE_FILE), self.keys, "fake-upstream-only",
                         upstream_base=self.upstream_base, use_request_bounds=True)
        self.ledger.adjust("interaction", "0.2", temporary=True)
        path = "/openai/interaction/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.keys['interaction']}"}
        model = {"model": "deepseek-flash", "max_tokens": 40,
                 "messages": [{"role": "user", "content": "unexpected-count"}]}
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.post(path, json=model, headers=headers)).status, 200)
            self.assertTrue(self.ledger.has_request_bound_overrun())
            self.assertFalse((await (await client.get("/balance", headers=headers)).json())["request_bounds"])
            self.assertEqual((await client.post(path, json=model, headers=headers)).status, 402)
        self.assertEqual(len(self.calls), 1)

    async def test_zero_rmb_gateway_can_still_deliver_a_verified_go_commission(self):
        self.ledger.adjust("interaction", "0", temporary=True)
        with tempfile.TemporaryDirectory() as folder, closing(Store(folder)) as store:
            owner, scope = "12345", "qq:FriendMessage:12345"
            jobs = CommissionedFiles(store)
            ident = jobs.accept(scope, owner, "original-request", {
                "cwd": r"D:\FishHome\commission-files", "source": "sample.csv", "instruction": "Count rows"},
                require_go=True, wake_agent=True)
            key = jobs._key(scope, ident)
            original = "sample.csv has two rows.\nFILE-EOF"
            task = store.get_setting(key)
            store.setting(key, {**task, "state": "completed", "result": original,
                                "usage": {"billing_source": "go", "go_receipts": [{"source": "go"}]}})

            statuses = []
            async def blocked_reply(_task):
                response = await self.post("/openai/interaction/v1/chat/completions", {
                    "model": "deepseek-flash", "messages": [{"role": "user", "content": "Report the result"}]})
                statuses.append(response.status)
                raise RuntimeError((await response.json())["error"]["code"])

            enqueue = AsyncMock(return_value={"fishhome_queue_id": "queued-once"})
            await jobs.dispatch_next(scope, owner, None, None, lambda _task: True,
                                     ready=lambda: False, wake=blocked_reply, enqueue=enqueue)
            self.assertEqual(statuses, [402])
            enqueue.assert_awaited_once()
            self.assertTrue(enqueue.call_args.args[1].endswith(original))
            self.assertEqual(store.get_setting(key)["delivery"], "queued")
        self.assertFalse(self.calls)
        self.assertEqual(self.ledger.snapshot()["ledgers"]["interaction"]["remaining"], "0")

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
