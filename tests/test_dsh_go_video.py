import asyncio
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from deploy.dsh_bridge.go_profile import MODEL, PROVIDER, render_file_go_patch, render_go_patch
from deploy.fish_cost import create_app
from deploy.fish_cost_ledger import Ledger
from deploy.dsh_bridge.go_relay import GoRelay, GoReceipt, subscription_receipt
from deploy.dsh_bridge.go_usage import normalize_usage
from deploy.dsh_bridge.media_asr import transcribe_audio
from deploy.dsh_bridge.go_video_task import run_go_task, run_search_task, run_video_task
from deploy.dsh_bridge.public_video import PRESET
from deploy.dsh_bridge.public_search import validate_query, validate_task_query


USAGE = {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0,
         "opencode": {"billing_source": "go", "allowance": 0.00031}}
BVID = "BV1g8Wq6DEXj"


class GoVideoTests(unittest.IsolatedAsyncioTestCase):
    def test_persistent_and_disposable_go_workers_use_the_native_deepseek_effort(self):
        for patch in (render_go_patch("session-test"), render_file_go_patch("interaction", Path("header.mjs"))):
            provider = patch[0]["config"]["providers"][PROVIDER]
            self.assertEqual(provider["reasoning"], "max")
            self.assertEqual(provider["models"][0]["reasoningEfforts"]["max"], "max")
            self.assertEqual(provider["compat"]["thinkingFormat"], "deepseek")
            self.assertTrue(provider["compat"]["requiresReasoningContentOnAssistantMessages"])

    def test_task_description_and_search_query_have_separate_limits(self):
        long_query = "资料" * 300
        self.assertEqual(validate_task_query(long_query), long_query)
        with self.assertRaises(ValueError):
            validate_query(long_query)
        with self.assertRaises(ValueError):
            validate_task_query("资" * 4001)

    async def test_timeout_reports_last_session_phase(self):
        async def call(method, request):
            if method == "session.create":
                return {"agentPreset": "test", "sessionId": "session"}
            if method == "session.selectModel":
                return {"selected": {"provider": PROVIDER, "model": MODEL}}
            if method == "session.prompt":
                return {"accepted": True}
            if method == "session.history":
                await asyncio.Event().wait()
            return {}

        phases = []
        with self.assertRaises(TimeoutError):
            await run_go_task(SimpleNamespace(call=call), ".", "test", "test", None, .01,
                              progress=phases.append)
        self.assertEqual(phases[-1], "waiting_for_result")

    def test_official_go_window_is_unknown_until_all_windows_are_present(self):
        self.assertEqual(normalize_usage(None)["status"], "unknown")
        item = {"percent": 100, "limited": True, "resetsAt": 1794789000000}
        value = normalize_usage({"go": {name: item for name in ("rolling", "weekly", "monthly")}}, now=7)
        self.assertEqual(value["status"], "limited")
        self.assertEqual(value["windows"]["weekly"]["resets_at_ms"], item["resetsAt"])
        self.assertEqual(value["checked_at"], 7)

    async def test_asr_free_only_exhaustion_returns_explicit_status(self):
        async def dashscope(request):
            actual = await request.json()
            self.assertEqual(actual["model"], "qwen3-asr-flash")
            return web.json_response({"code": "AllocationQuota.FreeTierOnly"}, status=403)
        app = web.Application()
        app.router.add_post("/", dashscope)
        async with TestServer(app) as server:
            result = await transcribe_audio({"mime": "audio/mpeg", "data": "fixture", "start": 0, "end": 3},
                                            "test-fixture", endpoint=str(server.make_url("/")))
        self.assertEqual(result, {"status": "free_quota_exhausted"})

    async def test_go_file_receipts_do_not_change_cny_ledgers(self):
        upstream_headers = []
        async def provider(request):
            upstream_headers.append(request.headers["x-opencode-session"])
            return web.Response(text=f'data: {json.dumps({"usage": USAGE})}\n\ndata: [DONE]\n\n',
                                content_type="text/event-stream")
        upstream = web.Application()
        upstream.router.add_post("/chat/completions", provider)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "cost.sqlite3")
            before = ledger.snapshot()
            async with TestServer(upstream) as tested:
                credentials = {"autonomous": "test-go-autonomous-credential", "interaction": "test-go-interaction-credential"}
                app = create_app(ledger, {}, {"autonomous": "test-rmb-autonomous-credential",
                    "interaction": "test-rmb-interaction-credential"}, None, offline=True,
                    go_files={"key": "provider-only", "credentials": credentials,
                              "upstream": str(tested.make_url("")).rstrip("/")})
                async with TestClient(TestServer(app)) as client:
                    headers = {"Authorization": "Bearer " + credentials["autonomous"], "x-opencode-session": "dsh-test"}
                    path = "/go/autonomous/v1/chat/completions"
                    model = {"model": MODEL, "stream": True, "messages": []}
                    wrong = await client.post(path, headers={"Authorization": "Bearer invalid", "x-opencode-session": "dsh-test"}, json=model)
                    self.assertEqual(wrong.status, 401)
                    good = await client.post(path, headers=headers, json=model)
                    self.assertEqual(good.status, 200)
                    await good.read()
                    receipt = await client.get("/go/autonomous/receipts/dsh-test", headers=headers)
                    self.assertEqual((await receipt.json())["receipts"][0]["source"], "go")
            self.assertEqual(upstream_headers, ["dsh-test"])
            self.assertEqual(before, ledger.snapshot())

    async def test_search_only_returns_cited_provider_results_without_snippets(self):
        summary = "结论。" * 410 + "\n来源：https://example.org/primary"
        call_text = summary

        async def call(method, request):
            if method == "session.create":
                return {"agentPreset": request["agentPreset"], "sessionId": "search"}
            if method == "session.selectModel":
                return {"selected": {"provider": PROVIDER, "model": MODEL}}
            if method == "session.prompt":
                call.request = request
                return {"accepted": True}
            if method == "session.history":
                return {"events": [
                    {"type": "user/message", "data": {"source": {"kind": "user", "rpcId": call.request["requestId"]}}},
                    {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": call_text}]}}},
                    {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
                ]}
            raise AssertionError(method)

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "sources.jsonl"
            evidence.write_text(json.dumps([
                {"url": "https://example.org/other", "title": "Unused", "snippet": "long excerpt"},
                {"url": "https://example.org/primary", "title": "Primary", "snippet": "not in the fish context"},
            ]) + "\n", encoding="utf-8")
            relay = SimpleNamespace(failure=None, receipts=[GoReceipt("go", Decimal("0.00031"), 10, 3)])
            result = await run_search_task(SimpleNamespace(call=call), directory, "public information", evidence, relay)
            self.assertIn("public information", call.request["content"][0]["text"])
            self.assertNotIn("long excerpt", call.request["content"][0]["text"])
            self.assertEqual(result["summary"], summary)
            self.assertEqual(result["evidence"], [{"url": "https://example.org/primary", "title": "Primary"}])
            self.assertIn("不超过 700 字", call.request["content"][0]["text"])
            for inline_summary in ("一句简报", None):
                with self.subTest(inline_summary=inline_summary):
                    call_text = f"## 简报\n{inline_summary}\n\n## 完整报告\n{summary}" if inline_summary else summary
                    result = await run_search_task(SimpleNamespace(call=call), directory, "public information",
                                                   evidence, relay, owner_report=True)
                    prompt = call.request["content"][0]["text"]
                    self.assertIn("## 完整报告", prompt)
                    self.assertNotIn("不超过 700 字", prompt)
                    self.assertEqual(result["full_report"], summary)
                    if inline_summary:
                        self.assertEqual(result["summary"], inline_summary)
                    else:
                        self.assertIn("查看完整报告", result["summary"])
                    self.assertEqual(result["evidence"], [{"url": "https://example.org/primary", "title": "Primary"}])

    async def test_relay_requires_go_receipts_and_keeps_real_key_out_of_worker(self):
        calls = []

        async def provider(request):
            calls.append((request.headers["Authorization"], request.headers["x-opencode-session"], await request.json()))
            usage = USAGE if len(calls) == 1 else {**USAGE, "cost": 1}
            return web.Response(text=f'data: {json.dumps({"usage": usage})}\n\ndata: [DONE]\n\n',
                                content_type="text/event-stream")

        app = web.Application()
        app.router.add_post("/chat/completions", provider)
        async with TestServer(app) as upstream:
            relay = GoRelay("provider-only-secret", "job-token-not-provider-key-01", "job-1",
                            upstream=str(upstream.make_url("")).rstrip("/"))
            async with TestClient(TestServer(relay.create_app())) as client:
                request = {"model": MODEL, "stream": True, "messages": []}
                route = "/v1/chat/completions"
                denied = await client.post(route, json=request)
                self.assertEqual(denied.status, 401)
                headers = {"Authorization": "Bearer job-token-not-provider-key-01", "x-opencode-session": "job-1"}
                for _ in range(2):
                    response = await client.post(route, json=request, headers=headers)
                    self.assertEqual(response.status, 200)
                    self.assertNotIn("provider-only-secret", await response.text())
                stopped = await client.post(route, json=request, headers=headers)
                self.assertEqual(stopped.status, 409)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ("Bearer provider-only-secret", "job-1", request | {"stream_options": {"include_usage": True}}))
        self.assertEqual(relay.receipts, [GoReceipt("go", Decimal("0.00031"), 10, 3)])

    async def test_missing_done_requires_finish_and_a_valid_subscription_receipt(self):
        async def provider(request):
            body = await request.json()
            events = [{"choices": [{"finish_reason": "stop"}]}] if body["messages"] else []
            events.append({"usage": USAGE})
            return web.Response(text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
                                content_type="text/event-stream")

        app = web.Application()
        app.router.add_post("/chat/completions", provider)
        async with TestServer(app) as upstream:
            for finished in (True, False):
                relay = GoRelay("provider-secret", "isolated-token-not-provider-key", "job",
                                upstream=str(upstream.make_url("")).rstrip("/"))
                async with TestClient(TestServer(relay.create_app())) as client:
                    response = await client.post("/v1/chat/completions", headers={
                        "Authorization": "Bearer isolated-token-not-provider-key", "x-opencode-session": "job"},
                        json={"model": MODEL, "stream": True,
                              "messages": [{"role": "user", "content": "test"}] if finished else []})
                    self.assertEqual("[DONE]" in await response.text(), finished)
                    self.assertEqual(bool(relay.receipts), finished)
                    self.assertEqual(bool(relay.failure), not finished)

    async def test_task_input_is_link_only_and_reply_is_distinct_from_personal_viewing(self):
        class Client:
            async def call(self, name, params):
                if name == "session.create":
                    self.assert_preset(params)
                    return {"agentPreset": PRESET, "sessionId": "worker"}
                if name == "session.selectModel":
                    return {"selected": params}
                if name == "session.prompt":
                    self.input = params
                    return {"accepted": True}
                return {"events": [
                    {"type": "user/message", "data": {"source": {"kind": "user", "rpcId": self.input["requestId"]}}},
                    {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": "代看摘要"}]}}},
                    {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
                ]}

            def assert_preset(self, params):
                assert params["agentPreset"] == PRESET

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.jsonl"
            source = f"https://www.bilibili.com/video/{BVID}?p=1"
            frame = {"source": source, "cid": 34, "sampling_range": {"start": 0, "end": 8},
                     "frame_times": [0, 7.5], "subtitle_count": 0}
            evidence.write_text(json.dumps(frame), encoding="utf-8")
            relay = SimpleNamespace(failure=None, receipts=[subscription_receipt(USAGE)])
            client = Client()
            agent = {"id": "d" * 16, "name": "细尾", "version": 2, "persona": "人格正文",
                     "guides": [{"id": "e" * 16, "title": "采样指南", "version": 3, "text": "工作指南正文"}]}
            result = await run_video_task(client, root, source, "核对 10 到 14 秒", evidence, relay, agent=agent)
            self.assertEqual(result["kind"], "dsh_video")
            self.assertEqual(result["summary"], "代看摘要")
            self.assertEqual(result["evidence"], [frame])
            self.assertEqual(result["billing"][0]["source"], "go")
            self.assertEqual([item["type"] for item in client.input["content"]], ["text"])
            self.assertIn(source, client.input["content"][0]["text"])
            self.assertIn("核对 10 到 14 秒", client.input["content"][0]["text"])
            self.assertIn("有指定片段时直接取该范围，不预看开头", client.input["content"][0]["text"])
            self.assertIn("人格正文", client.input["content"][0]["text"])
            self.assertIn("工作指南正文", client.input["content"][0]["text"])
            self.assertEqual(result["agent"], {"id": "d" * 16, "name": "细尾", "version": 2,
                "guides": [{"id": "e" * 16, "title": "采样指南", "version": 3}]})
            evidence.write_text(json.dumps({**frame, "source": source[:-1] + "2"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "来源不一致"):
                await run_video_task(Client(), root, source, "核对开头", evidence, relay)
