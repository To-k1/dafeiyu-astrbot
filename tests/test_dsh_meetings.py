import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from unittest.mock import AsyncMock, patch

from test_bili_activity import core
from bili_activity_test.dsh_meetings import DshMeetings


SCOPE = "qq:FriendMessage:12345"
PROFILES = [
    {"id": "1" * 16, "name": "甲", "persona": "独立论证甲", "version": 1},
    {"id": "2" * 16, "name": "乙", "persona": "独立论证乙", "version": 2},
]


class MeetingRunner:
    model_route = "go"

    def __init__(self):
        self.sessions = {}
        self.sent = []
        self.concurrent = 0
        self.overlap = 0
        self.disconnect = False
        self.hold = set()
        self.accepted = asyncio.Event()
        self.request_ids = {}
        self.cancelled = []

    async def call(self, method, args):
        if method == "session.create":
            ident = str(len(self.sessions) + 1)
            self.sessions[ident] = []
            return {"sessionId": ident, "agentPreset": args["agentPreset"]}
        if method == "session.selectModel":
            return {"selected": args}
        if method == "session.state":
            return {"running": args["sessionId"] in self.hold and args["sessionId"] in self.request_ids}
        if method == "session.history":
            return {"events": self.sessions[args["sessionId"]]}
        if method == "session.cancel":
            ident = args["sessionId"]
            self.cancelled.append(ident)
            self.hold.discard(ident)
            self.sessions[ident].append({"type": "turn/end", "data": {"reason": {"kind": "aborted"}}})
            return {}
        if method != "session.prompt":
            raise AssertionError(method)
        ident, request = args["sessionId"], args["requestId"]
        self.sent.append((ident, request, args["content"][0]["text"]))
        self.concurrent += 1
        self.overlap = max(self.overlap, self.concurrent)
        try:
            await asyncio.sleep(0)
            self.request_ids[ident] = request
            self.sessions[ident].append({"type": "user/message", "data": {"source": {"kind": "user", "rpcId": request}}})
            if ident in self.hold:
                self.accepted.set()
                return {"accepted": True}
            answer = "支持方案甲的独立证据" if ident == "1" else "反对方案甲的独立证据"
            if "本轮 2" in args["content"][0]["text"]:
                answer = json.dumps({"position": answer, "disagreements": ["结论相反"],
                                     "cited_sources": ["共享表，待核对"]}, ensure_ascii=False)
            self.sessions[ident].extend([
                {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": answer}]}}},
                {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
            ])
            if self.disconnect:
                raise OSError("transport disconnected after accept")
            return {"accepted": True}
        finally:
            self.concurrent -= 1

    async def go_receipts(self, session):
        return {"receipts": [1] * (len(self.sessions[session]) // 3)}

    async def close(self):
        pass


class MeetingTests(unittest.IsolatedAsyncioTestCase):
    async def test_timed_reports_wait_for_interval_and_confirmed_round(self):
        runner = MeetingRunner()
        notify = AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            with patch("bili_activity_test.dsh_meetings.time", return_value=1000) as now:
                meetings = DshMeetings(store, SCOPE)
                ident = meetings.create("按时间汇报", PROFILES, "background", 3, 0, 10, "timed")["id"]
                now.return_value += 9 * 60
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                notify.assert_not_awaited()
                now.return_value += 60
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                self.assertEqual(notify.await_args.args[1]["kind"], "progress")
                self.assertEqual(meetings.get(ident)["round"], 2)

    async def test_members_reuse_independent_sessions_exchange_views_and_resume(self):
        runner = MeetingRunner()
        notify = AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                row = meetings.create("核对方案甲", PROFILES, "interactive", 2, 1, 0, "msg-1")
                ident = row["id"]
                self.assertEqual(meetings.create("核对方案甲", PROFILES, "interactive", 2, 1, 0, "msg-1")["id"], ident)
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                self.assertEqual(runner.overlap, 2)
                self.assertEqual(meetings.control(ident, "pause")["status"], "paused")
                meetings.post(ident, "请把共享资料中的表作为出处", "msg-2")
                meetings.post(ident, "请把共享资料中的表作为出处", "msg-2")
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                self.assertEqual(len(runner.sent), 2)
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                meetings.control(ident, "resume")
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                row = meetings.get(ident)
                self.assertEqual(row["status"], "completed")
                self.assertEqual(len(runner.sessions), 2)
                self.assertEqual(len(runner.sent), 4)
                self.assertIn("反对方案甲的独立证据", runner.sent[2][2])
                self.assertIn("支持方案甲的独立证据", runner.sent[3][2])
                self.assertIn("请把共享资料中的表作为出处", runner.sent[2][2])
                self.assertEqual(len([item for item in row["transcript"] if item["kind"] == "host"]), 1)
                self.assertEqual(len(row["summary"]["conclusions"]), 2)
                self.assertEqual(row["summary"]["conclusions"][0]["participant_record"]["disagreements"], ["结论相反"])
                self.assertEqual([call.args[1]["kind"] for call in notify.await_args_list], ["progress", "completed"])
                self.assertEqual(meetings.control(ident, "extend", rounds=1)["status"], "queued")
                with self.assertRaises(ValueError):
                    DshMeetings(store, "other").get(ident)

    async def test_disconnect_after_accept_polls_that_request_after_restart(self):
        runner = MeetingRunner()
        runner.disconnect = True
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                ident = meetings.create("证据是否一致", PROFILES, "background", 1, 0, 0, "m2")["id"]
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
                self.assertEqual(len(runner.sent), 2)
                self.assertEqual(meetings.get(ident)["status"], "running")
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
                row = meetings.get(ident)
                self.assertEqual(row["status"], "completed")
                self.assertEqual(len(runner.sent), 2)
                self.assertEqual(len(row["transcript"]), 2)
                self.assertEqual([notice["delivery"] for notice in row["notices"]], ["pending"])

    async def test_stop_cancels_live_request_and_keeps_other_members_verified_reply(self):
        runner = MeetingRunner()
        runner.hold.add("2")
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("停止演练", PROFILES, "background", 2, 0, 0, "m3")["id"]
            running = asyncio.create_task(meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None))
            await asyncio.wait_for(runner.accepted.wait(), 2)
            self.assertEqual(meetings.control(ident, "stop")["status"], "stopping")
            await asyncio.wait_for(running, 4)
            row = meetings.get(ident)
            self.assertEqual(row["status"], "stopped")
            self.assertEqual(runner.cancelled, ["2"])
            self.assertEqual([entry["agent_id"] for entry in row["transcript"]], ["1" * 16])
            self.assertEqual(row["notices"][0]["conclusions"][0]["agent_id"], "1" * 16)

    async def test_failed_notice_is_readable_as_uncertain_after_restart_and_not_replayed(self):
        runner = MeetingRunner()
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                ident = meetings.create("送达状态", PROFILES, "background", 1, 0, 0, "m4")["id"]
                notify = AsyncMock(side_effect=asyncio.CancelledError)
                with self.assertRaises(asyncio.CancelledError):
                    await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                self.assertEqual(meetings.get(ident)["notices"][0]["delivery"], "sending")
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                meetings.recover()
                notify = AsyncMock(return_value=True)
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, notify)
                self.assertEqual(meetings.get(ident)["notices"][0]["delivery"], "uncertain")
                notify.assert_not_awaited()
