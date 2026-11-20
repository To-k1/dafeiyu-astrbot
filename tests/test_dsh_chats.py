import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from unittest.mock import AsyncMock, patch

import importlib.util
import sys
import types
from pathlib import Path

plugin = Path(__file__).resolve().parents[1] / "plugin" / "astrbot_plugin_bili_activity"
if "bili_activity_test" not in sys.modules:
    package = types.ModuleType("bili_activity_test")
    package.__path__ = [str(plugin)]
    sys.modules[package.__name__] = package
from bili_activity_test import core
from bili_activity_test.dsh_chats import DshChats
from bili_activity_test.dsh_roster import DshRoster
from bili_activity_test.dsh_history_archive import copy_from_archive, read_archive, snapshot

SCOPE = "qq:FriendMessage:12345"


class Runner:
    model_route = "go"
    def __init__(self):
        self.prompts = []
        self.done = set()
    async def call(self, method, args):
        if method == "session.create":
            return {"sessionId": "session-" + str(len(self.prompts)), "agentPreset": "fishhome-chat"}
        if method == "session.selectModel":
            return {"selected": args}
        if method == "session.prompt":
            self.prompts.append(args)
            return {"accepted": True}
        if method == "session.state":
            return {"running": True}
        if method == "session.history":
            events = []
            for prompt in self.prompts:
                if prompt["sessionId"] == args["sessionId"] and prompt["requestId"] in self.done:
                    events.extend([{"type": "user/message", "data": {"source": {"kind": "user", "rpcId": prompt["requestId"]}}},
                                   {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": "reply:" + prompt["content"][0]["text"]}]}}},
                                   {"type": "turn/end", "data": {"reason": {"kind": "completed"}}}])
            return {"events": events}
        raise AssertionError(method)
    async def go_receipts(self, session):
        return {"receipts": [p["requestId"] for p in self.prompts if p["sessionId"] == session and p["requestId"] in self.done]}
    async def go_quota_available(self):
        return True
    async def close(self):
        pass


class ChatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_chat_returns_anonymous_evidence_to_the_same_native_session(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            (Path(folder) / "shared-language.json").write_text(json.dumps({
                "enabled": True, "filename": "language.txt", "text": "测试角色的共同语言"
            }), encoding="utf-8")
            agent = DshRoster(store, SCOPE).create("潮汐笔记", "私有角色文本")['id']
            chats = DshChats(store, SCOPE)
            job = chats.enqueue("去查两个出处，回来表达你的判断", agent, "source-1")
            runner, lock = Runner(), asyncio.Lock()
            calls = []
            queued = set()

            async def worker(method, ident, body=None):
                calls.append((method, ident, body))
                if method == "PUT":
                    queued.add(ident)
                    return {"id": ident, "state": "queued"}
                if ident not in queued:
                    return {"id": ident, "state": "not_found"}
                return {"id": ident, "state": "completed", "report": {
                    "kind": "dsh_web_search", "summary": "evidence only", "billing": [{"source": "go"}],
                    "evidence": [{"url": "https://docs.python.org/3/", "title": "Python"}]}}

            runner.delegate_public = worker
            runner.delegate_kinds = ("web",)
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, None,
                    lambda scope, ident: lock)
            async def wait_for_turn(client, turn):
                runner.done.add(turn["request_id"])
                if len(runner.prompts) == 1:
                    return ('<fishhome_delegate>[{"kind":"web","query":"first evidence"},'
                            '{"kind":"web","query":"second evidence"}]</fishhome_delegate>')
                return "我的判断有可靠来源。"

            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=wait_for_turn):
                await chats.dispatch_next(*args)
                self.assertEqual(chats.get(job["id"])["status"], "running")
                self.assertEqual(len(runner.prompts), 1)
                # Simulate a fresh scheduler resuming the saved parent and child IDs.
                chats = DshChats(store, SCOPE)
                await chats.dispatch_next(*args)
                await chats.dispatch_next(*args)
            completed = chats.get(job["id"])
            self.assertEqual((completed["status"], completed["result"], completed["go_receipt_count"]),
                             ("completed", "我的判断有可靠来源。", 2))
            self.assertEqual(len(runner.prompts), 2)
            sent = runner.prompts[0]["content"][0]["text"]
            self.assertIn("私有角色文本", sent)
            self.assertIn("测试角色的共同语言", sent)
            self.assertEqual(runner.prompts[0]["sessionId"], runner.prompts[1]["sessionId"])
            self.assertIn("https://docs.python.org/3/", runner.prompts[1]["content"][0]["text"])
            self.assertEqual([body for method, _, body in calls if method == "PUT"], [
                {"kind": "web", "query": "first evidence"}, {"kind": "web", "query": "second evidence"}])
            self.assertEqual(completed["tool_delegations"][0]["actor"], agent)

    async def test_legacy_chat_uses_exact_request_on_original_route_and_releases_the_next(self):
        for exact in (True, False):
            with self.subTest(exact=exact), tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
                class CommissionRunner(Runner):
                    async def call(self, method, args):
                        if method == "session.state":
                            return {"running": False}
                        result = await super().call(method, args)
                        if method == "session.prompt":
                            self.done.add(args["requestId"])
                        return result

                class MissingRunner(Runner):
                    async def call(self, method, args):
                        raise ValueError("DSH 拒绝 session.state（session/not-found）")

                roster = DshRoster(store, SCOPE)
                agent = roster.create("A", "persona")["id"]
                logical = roster.new_session(agent, "旧话题")["id"]
                roster.set_native_session(agent, logical, "legacy-native")
                chats = DshChats(store, SCOPE)
                old = chats.enqueue("原文", agent, "previous", autonomous=True, session_id=logical)
                old.update(status="running", turn={"session_id": "legacy-native", "request_id": "original",
                    "model_route": "go", "receipt_count": 0, "version": 1})
                chats._save(old)
                next_chat = chats.enqueue("后续问句", agent, "new", autonomous=True, session_id=logical)
                runner = CommissionRunner()
                if exact:
                    runner.prompts.append({"sessionId": "legacy-native", "requestId": "original",
                                           "content": [{"type": "text", "text": "旧的已提交正文"}]})
                    runner.done.add("original")
                lane = {"commission": runner, "life": MissingRunner()}
                lock = asyncio.Lock()
                settings = (lambda row: lane[row["execution_route"]], lambda row: "workspace",
                            lambda row: True, None, lambda scope, ident: lock)
                await chats.dispatch_next(*settings, resolve_routes=True)
                await chats.dispatch_next(*settings, resolve_routes=True)
                old = chats.get(old["id"])
                self.assertEqual(old["status"], "completed" if exact else "needs_review")
                if exact:
                    self.assertEqual(old["request_id"], "original")
                else:
                    self.assertEqual(old["failure"]["code"], "native_route_unconfirmed")
                    self.assertEqual(old["delivery"], "uncertain")
                self.assertTrue(old["autonomous"])
                self.assertEqual(roster._session(roster.get(agent), logical)["native_route"], "commission")
                self.assertEqual(chats.get(next_chat["id"])["turn"]["execution_route"], "commission")
                self.assertEqual(chats.get(next_chat["id"])["status"], "completed")
                self.assertEqual(len(runner.prompts), 2 if exact else 1)

    async def test_copied_session_continues_in_new_native_session_without_replaying_jobs(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            original = roster.create("原角色", "人格")["id"]
            session = roster.new_session(original, "潮汐")
            roster.set_native_session(original, session["id"], "old-dsh-session")
            chats = DshChats(store, SCOPE)
            first = chats.enqueue("能说明当时的约定吗？", original, "old-request")
            first.update(status="completed", result="只看原始日期", delivery="notified")
            chats._save(first)
            copied = copy_from_archive(store, SCOPE, read_archive(snapshot(store, SCOPE, original)))
            copy_id = copied["id"]
            self.assertEqual(len(chats.list()), 1)
            logical = roster.list_sessions(copy_id)[0]
            self.assertNotEqual(logical["id"], session["id"])
            self.assertIsNone(logical["native_session"])
            copy_turn = chats.enqueue("继续聊", copy_id, "new-request")
            runner, lock = Runner(), asyncio.Lock()
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None,
                                          lambda scope, agent: lock)
            self.assertEqual(len(runner.prompts), 1)
            self.assertNotEqual(runner.prompts[0]["sessionId"], "old-dsh-session")
            self.assertIn("只看原始日期", runner.prompts[0]["content"][0]["text"])
            self.assertEqual(chats.get(copy_turn["id"])["agent_id"], copy_id)
            self.assertEqual(roster.list_sessions(original)[0]["native_session"], "old-dsh-session")

    async def test_historical_review_recovers_original_without_paid_continuation(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            chats = DshChats(store, SCOPE)
            job = chats.enqueue("question", agent, "one")
            runner, lock = Runner(), asyncio.Lock()
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, None,
                    lambda scope, ident: lock)
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            runner.done.add(runner.prompts[0]["requestId"])
            row = chats.get(job["id"])
            row.update(status="needs_review", delivery="notified", agent_reply="old failure notice",
                       failure={"code": "billing_unconfirmed", "detail": "missing"}, memory_protocol_required=True)
            chats._save(row)
            memory = unittest.mock.Mock()
            memory.decode.return_value = {"text": "original answer", "query": "history", "memories": [], "present": False}
            runner.team_memory = (memory, object())
            await chats.dispatch_next(*args)
            result = chats.get(job["id"])
            self.assertEqual((result["status"], result["result"]), ("completed", "original answer"))
            self.assertEqual((result["delivery"], result["notification_revision"]), ("pending", "billing_confirmed"))
            self.assertNotIn("agent_reply", result)
            self.assertEqual(len(runner.prompts), 1)
            memory.prepare.assert_not_called()
            memory.remember_turn.assert_not_called()

    async def test_completed_turn_recovers_delayed_billing_without_resending(self):
        class DelayedRunner(Runner):
            billing_ready = False
            async def go_receipts(self, session):
                if not self.billing_ready:
                    return {"receipts": [], "needs_review": False}
                return await super().go_receipts(session)

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            chats = DshChats(store, SCOPE)
            job = chats.enqueue("question", agent, "one")
            runner, lock = DelayedRunner(), asyncio.Lock()
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, None,
                    lambda scope, ident: lock)
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            runner.done.add(runner.prompts[0]["requestId"])
            await chats.dispatch_next(*args)
            review = chats.get(job["id"])
            self.assertEqual(review["failure"]["code"], "billing_unconfirmed")
            self.assertIn("question", review["confirmed_turn"]["reply"])
            same = chats.enqueue("later", agent, "two")
            await chats.dispatch_next(*args)
            self.assertEqual(chats.get(same["id"])["status"], "queued")
            self.assertEqual(len(runner.prompts), 1)
            runner.billing_ready = True
            review = chats.get(job["id"])
            review["billing_retry_after"] = 0
            chats._save(review)
            chats = DshChats(store, SCOPE)
            await chats.dispatch_next(*args)
            result = chats.get(job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["go_receipt_count"], 1)
            self.assertIn("question", result["result"])
            self.assertEqual(len(runner.prompts), 1)

    async def test_verified_review_does_not_block_another_session(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            chats = DshChats(store, SCOPE)
            first = chats.enqueue("first", agent, "one")
            runner, lock = Runner(), asyncio.Lock()
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, None,
                    lambda scope, ident: lock)
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            row = chats.get(first["id"])
            row.update(status="needs_review", failure={"code": "billing_unconfirmed", "detail": "missing"},
                       confirmed_turn={"request_id": row["turn"]["request_id"], "reply": "answer"},
                       billing_retry_after=float("inf"))
            chats._save(row)
            session = roster.new_session(agent)
            second = chats.enqueue("second", agent, "two", session_id=session["id"])
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            self.assertEqual(chats.get(second["id"])["status"], "running")
            self.assertEqual(len(runner.prompts), 2)
            self.assertNotEqual(runner.prompts[0]["sessionId"], runner.prompts[1]["sessionId"])

    async def test_restart_queue_fifo_original_reply_and_notification_retry(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            chats = DshChats(store, SCOPE)
            first = chats.enqueue("long original", agent, "source-1")
            self.assertEqual(first["id"], chats.enqueue("long original", agent, "source-1")["id"])
            runner, lock = Runner(), asyncio.Lock()
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, None, lambda scope, agent: lock)
            # Simulate a wait timeout without delaying the test; the prompt has already been accepted.
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            request = runner.prompts[0]["requestId"]
            self.assertEqual(chats.get(first["id"])["status"], "running")
            second = chats.enqueue("new instruction", agent, "source-2")
            chats = DshChats(store, SCOPE)
            chats.recover()
            runner.done.add(request)
            notify = AsyncMock(side_effect=[None, "sent", "sent"])
            args = (args[0], args[1], args[2], notify, args[4])
            await chats.dispatch_next(*args)
            self.assertEqual(len(runner.prompts), 1)
            self.assertIn("long original", chats.get(first["id"])["result"])
            self.assertEqual(chats.get(first["id"])["request_id"], request)
            self.assertEqual(chats.get(second["id"])["status"], "queued")
            self.assertEqual(chats.get(first["id"])["delivery"], "pending")
            with patch("bili_activity_test.dsh_chats._wait_for_turn", new=AsyncMock(side_effect=TimeoutError)):
                await chats.dispatch_next(*args)
            self.assertEqual(len(runner.prompts), 2)
            self.assertIn("new instruction", runner.prompts[1]["content"][0]["text"])
            self.assertEqual(chats.get(first["id"])["delivery"], "notified")
            runner.done.add(runner.prompts[1]["requestId"])
            await chats.dispatch_next(*args)
            self.assertIn("new instruction", chats.get(second["id"])["result"])
            self.assertEqual(notify.await_count, 3)
            self.assertEqual(DshChats(store, "other").list(), [])

    async def test_legacy_adopt_and_uncertain_notice(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            profile = roster.get(agent)
            profile.update(chat_session="old", pending_chat={"session_id": "old", "request_id": "old-id", "model_route": "go", "receipt_count": 0, "version": 1})
            roster.save(profile)
            chats = DshChats(store, SCOPE)
            new = chats.enqueue("new message", agent, "source")
            rows = chats._rows()
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]["legacy"])
            self.assertIsNone(rows[0]["message"])
            self.assertEqual(rows[1]["id"], new["id"])
            chats.recover()
            self.assertEqual(len(chats.list()), 2)
            row = chats.get(rows[0]["id"])
            row.update(status="completed", result="old reply", delivery="sending")
            chats._save(row)
            chats.recover()
            self.assertEqual(chats.get(row["id"])["delivery"], "uncertain")
            self.assertEqual(chats.get(row["id"])["result"], "old reply")


    async def test_parallel_agents_and_anonymous_with_notification_exception(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            first = roster.create("A", "persona")["id"]
            second = roster.create("B", "persona")["id"]
            chats = DshChats(store, SCOPE)
            jobs = [chats.enqueue("one", first, "one"), chats.enqueue("two", second, "two"),
                    chats.enqueue("anonymous", "", "three")]
            runner = Runner()
            locks = {}
            def lock(scope, agent):
                return locks.setdefault(agent, asyncio.Lock())
            started = []
            ready = asyncio.Event()
            async def wait(client, turn):
                started.append(turn["request_id"])
                if len(started) == 3:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 1)
                client.done.add(turn["request_id"])
                return "original:" + turn["request_id"]
            async def failing_notice(row):
                chats.update_notification(row["id"], agent_reply="prepared")
                raise RuntimeError("delivery unavailable")
            args = (lambda row: runner, lambda row: "workspace", lambda row: True, failing_notice, lock)
            with patch("bili_activity_test.dsh_chats._wait_for_turn", side_effect=wait):
                await chats.dispatch_next(*args)
            for job in jobs:
                row = chats.get(job["id"])
                self.assertEqual(row["status"], "completed")
                self.assertEqual(row["result"], "original:" + row["request_id"])
                self.assertEqual(row["agent_reply"], "prepared")
                self.assertEqual(row["delivery"], "pending")
                self.assertEqual(row["notification_error"]["error_type"], "RuntimeError")
            successful = AsyncMock(return_value={"summary": "sent"})
            await chats.dispatch_next(*args[:3], successful, lock)
            await chats.dispatch_next(*args[:3], successful, lock)
            self.assertEqual(successful.await_count, 3)
            self.assertTrue(all(chats.get(job["id"])["delivery"] == "notified" for job in jobs))
            self.assertTrue(all("notification_error" not in chats.get(job["id"]) for job in jobs))


    async def test_go_wait_retry_dedup_and_failure_notice(self):
        from bili_activity_test.dsh_task import GoQuotaWaiting, DshTaskFailure
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create("A", "persona")["id"]
            other = roster.create("B", "persona")["id"]
            chats = DshChats(store, SCOPE)
            job = chats.enqueue("question", agent, "same")
            self.assertEqual(job["id"], chats.enqueue("question", agent, "same")["id"])
            self.assertNotEqual(job["id"], chats.enqueue("different", agent, "same")["id"])
            self.assertNotEqual(job["id"], chats.enqueue("question", other, "same")["id"])
            runner = Runner()
            locks = {}
            def lock(scope, ident):
                return locks.setdefault(ident, asyncio.Lock())
            # Focus on the first agent while retaining the other queue entry.
            allowed = lambda row: row["agent_id"] == agent
            notify = AsyncMock(return_value="sent")
            args = (lambda row: runner, lambda row: "workspace", allowed, notify, lock)
            with patch("bili_activity_test.dsh_chats._wait_for_turn", side_effect=GoQuotaWaiting("quota")):
                await chats.dispatch_next(*args)
            row = chats.get(job["id"])
            self.assertEqual(row["status"], "go_wait")
            self.assertNotIn("turn", row)
            self.assertEqual(row["attempts"][0]["request_id"], runner.prompts[0]["requestId"])
            self.assertNotIn("pending_chat", roster.get(agent))
            with patch("bili_activity_test.dsh_chats._wait_for_turn", side_effect=DshTaskFailure("unconfirmed", "missing")):
                await chats.dispatch_next(*args)
            row = chats.get(job["id"])
            self.assertEqual(row["status"], "needs_review")
            self.assertNotEqual(runner.prompts[0]["requestId"], runner.prompts[1]["requestId"])
            self.assertEqual(row["delivery"], "notified")
            self.assertEqual(notify.await_args.args[0]["failure"]["code"], "unconfirmed")
            await chats.dispatch_next(*args)
            self.assertEqual(len(runner.prompts), 2)

    async def test_dispatch_records_unexpected_poll_error(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            chats = DshChats(store, SCOPE)
            job = chats.enqueue("hi", "", "source")
            def broken(row):
                raise OSError("offline")
            lock = asyncio.Lock()
            await chats.dispatch_next(broken, lambda row: "workspace", lambda row: True, None,
                                      lambda scope, ident: lock)
            row = chats.get(job["id"])
            self.assertEqual(row["status"], "queued")
            self.assertEqual(row["poll"]["error_type"], "OSError")

    async def test_fast_result_notified_before_slow_agent_finishes(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            fast = roster.create("Fast", "persona")["id"]
            slow = roster.create("Slow", "persona")["id"]
            chats = DshChats(store, SCOPE)
            fast_job = chats.enqueue("fast question", fast, "fast")
            slow_job = chats.enqueue("slow question", slow, "slow")
            runner, locks = Runner(), {}
            def lock(scope, agent):
                return locks.setdefault(agent, asyncio.Lock())
            slow_started, release_slow, fast_notified = asyncio.Event(), asyncio.Event(), asyncio.Event()
            async def wait(client, turn):
                prompt = next(item for item in client.prompts if item["requestId"] == turn["request_id"])
                if "slow question" in prompt["content"][0]["text"]:
                    slow_started.set()
                    await release_slow.wait()
                else:
                    await slow_started.wait()
                client.done.add(turn["request_id"])
                return "reply"
            async def notify(row):
                if row["id"] == fast_job["id"]:
                    fast_notified.set()
                return "delivered"
            with patch("bili_activity_test.dsh_chats._wait_for_turn", side_effect=wait):
                task = asyncio.create_task(chats.dispatch_next(lambda row: runner, lambda row: "workspace",
                                                               lambda row: True, notify, lock))
                try:
                    await asyncio.wait_for(fast_notified.wait(), 2)
                    self.assertFalse(task.done())
                    self.assertEqual(chats.get(fast_job["id"])["delivery"], "notified")
                    self.assertEqual(chats.get(slow_job["id"])["status"], "running")
                finally:
                    release_slow.set()
                    await task
            self.assertEqual(chats.get(slow_job["id"])["delivery"], "notified")

