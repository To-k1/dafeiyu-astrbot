import asyncio
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

