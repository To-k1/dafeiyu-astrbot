import tempfile
import unittest
from contextlib import closing
from pathlib import PureWindowsPath
from unittest.mock import AsyncMock

from test_bili_activity import core
from bili_activity_test.commissioned import CommissionedFiles


SCOPE = "qq:FriendMessage:12345"
PLAN = {"cwd": r"C:\Runner\commission-files", "source": "sample.csv", "instruction": "Summarize the sample."}


class FileRunner:
    def __init__(self, task, preset="fishhome-files"):
        self.task, self.preset = task, preset
        self.calls = []

    async def call(self, method, args):
        self.calls.append((method, args))
        if method == "session.create":
            return {"sessionId": "job-1", "agentPreset": self.preset}
        if method == "session.selectModel":
            return {"selected": args}
        if method == "session.prompt":
            return {"accepted": True}
        if method == "session.state":
            return {}
        if method == "session.history":
            return {"events": [
                {"type": "user/message", "data": {"source": {"kind": "user", "rpcId": self.task["request_id"]}}},
                {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
            ]}
        if method == "workspaceFiles.read":
            path = args["path"]
            content = {self.task["report"]: "sample.csv has two rows.",
                       self.task["sources"]: '{"source":"sample.csv"}',
                       self.task["source"]: "a,b\n1,2\n"}
            return {"text": content[path], "eof": True, "absolutePath": str(PureWindowsPath(PLAN["cwd"]) / path)}
        if method == "session.projections":
            return {"values": {"tokenUsage": {"input": 50, "output": 20}}}
        raise AssertionError(method)

    async def close(self):
        pass


class CommissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_accept_restart_execute_verify_and_deliver_once(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                jobs = CommissionedFiles(store)
                ident = jobs.accept(SCOPE, "12345", "1", PLAN)
                self.assertEqual(jobs.accept(SCOPE, "12345", "1", PLAN), ident)
                self.assertFalse(jobs.cancel(SCOPE, "999", ident))
                cancelled = jobs.accept(SCOPE, "12345", "2", PLAN)
                self.assertTrue(jobs.cancel(SCOPE, "12345", cancelled))
            with closing(core.Store(folder)) as store:
                jobs = CommissionedFiles(store)
                task = jobs.list(SCOPE, "12345", pending_only=True)[0]
                self.assertEqual(task["source_message_id"], "1")
                self.assertEqual(task["billing"], "interaction")
                runner = FileRunner(task)
                send = AsyncMock(return_value={"message_id": "42"})
                await jobs.dispatch_next(SCOPE, "999", lambda _: runner, send, lambda _: True)
                send.assert_not_awaited()
                await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
                await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
                self.assertEqual(runner.calls[0][1], {"cwd": PLAN["cwd"], "agentPreset": "fishhome-files"})
                self.assertEqual(sum(method == "session.prompt" for method, _ in runner.calls), 1)
                send.assert_awaited_once()
                self.assertIn("sample.csv has two rows.", send.call_args.args[1])
                task = next(job for job in jobs.list(SCOPE, "12345") if job["id"] == ident)
                self.assertEqual((task["state"], task["delivery"], task["message_id"]), ("completed", "sent", "42"))

    async def test_wrong_preset_stops_paid_work_and_unknown_delivery_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            jobs.accept(SCOPE, "12345", "1", PLAN)
            runner = FileRunner(jobs.list(SCOPE, "12345")[0], preset="standard")
            send = AsyncMock(side_effect=TimeoutError)
            with self.assertRaises(TimeoutError):
                await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
            self.assertEqual([method for method, _ in runner.calls], ["session.create"])
            task = jobs.list(SCOPE, "12345")[0]
            self.assertEqual((task["state"], task["delivery"]), ("needs_review", "uncertain"))
            send.assert_awaited_once()
