import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from time import time
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
        self.replies = {}
        self.errors = set()
        self.delayed_receipts = set()

    def finish(self, ident, answer):
        self.sessions[ident].extend([
            {"type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": answer}]}}},
            {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
        ])

    async def call(self, method, args, timeout=None):
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
            if ident in self.errors:
                self.sessions[ident].append({"type": "turn/end", "data": {"reason": {"kind": "error"}}})
                return {"accepted": True}
            answer = self.replies.get(ident, "支持方案甲的独立证据" if ident == "1" else "反对方案甲的独立证据")
            if ident not in self.replies and "本轮 2" in args["content"][0]["text"]:
                answer = json.dumps({"position": answer, "disagreements": ["结论相反"],
                                     "cited_sources": ["共享表，待核对"]}, ensure_ascii=False)
            self.finish(ident, answer)
            if self.disconnect:
                raise OSError("transport disconnected after accept")
            return {"accepted": True}
        finally:
            self.concurrent -= 1

    async def go_receipts(self, session):
        return {"receipts": [1] * (len(self.sessions[session]) // 3 if session not in self.delayed_receipts else 0)}

    async def close(self):
        pass


class MeetingTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_web_post_reaches_unstarted_member_without_restarting_first(self):
        runner = MeetingRunner()
        runner.hold.add("1")
        release_second = asyncio.Event()

        async def recall(client, member_id, topic, turn):
            if member_id == PROFILES[1]["id"]:
                await release_second.wait()
            return ""

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("只讨论一轮", PROFILES, "background", 1, 0, 0, "web-test")["id"]
            with patch("bili_activity_test.dsh_meetings._memory_prepare", side_effect=recall):
                task = asyncio.create_task(meetings.dispatch_next(
                    lambda _: runner, lambda _: "workspace", lambda _: True, None))
                await asyncio.wait_for(runner.accepted.wait(), 2)
                before = meetings._get(ident)
                readonly = core.Store(folder, read_only=True)
                with closing(readonly.db):
                    self.assertEqual([m["status"] for m in DshMeetings(readonly, SCOPE).live(ident)["members"]],
                                     ["thinking", "waiting"])
                self.assertEqual(meetings._get(ident), before)
                source = "web:" + "a" * 32
                from_console = core.Store(folder, existing=True)
                with closing(from_console.db):
                    DshMeetings(from_console, SCOPE).post(ident, "请核对页码", source, owner_post=True)
                    DshMeetings(from_console, SCOPE).post(ident, "请核对页码", source, owner_post=True)
                runner.finish("1", "已有发言")
                runner.hold.clear()
                release_second.set()
                await asyncio.wait_for(task, 4)
            self.assertEqual(len(runner.sent), 2)
            self.assertNotIn("请核对页码", runner.sent[0][2])
            self.assertIn("请核对页码", runner.sent[1][2])
            state = meetings.live(ident)
            self.assertEqual(state["status"], "completed")
            self.assertFalse(state["can_post"])
            self.assertEqual(state["owner_receipts"], {source: [PROFILES[1]["id"]]})
            self.assertEqual([post["name"] for post in state["transcript"] if post["kind"] == "host"], ["主人"])

    async def test_verified_material_is_in_prompts_and_failed_go_session_is_retired(self):
        runner = MeetingRunner()
        runner.errors.add("1")
        receipts = runner.go_receipts

        async def billing(session):
            return {"receipts": [], "needs_review": True} if session == "1" and runner.sessions["1"] else await receipts(session)

        runner.go_receipts = billing
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            session = meetings.create("讨论证据", PROFILES, "background", 2, 0, 0, "material",
                materials=[{"path": "shared/材料.txt", "text": "FISHHOME-MATERIAL-EOF"}])
            args = (lambda _: runner, lambda _: "/workspace", lambda _: True, None)
            await meetings.dispatch_next(*args)
            await meetings.dispatch_next(*args)
            result = meetings.get(session["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["unresolved_turns"][0]["code"], "billing_unconfirmed")
            self.assertEqual([sid for sid, _, _ in runner.sent].count("1"), 1)
            self.assertTrue(any(sid == "3" for sid, _, _ in runner.sent))
            self.assertTrue(all("FISHHOME-MATERIAL-EOF" in text for _, _, text in runner.sent))
            self.assertEqual(result["materials"], [{"path": "shared/材料.txt"}])

    async def test_member_delegates_and_shares_only_its_confirmed_statement(self):
        runner = MeetingRunner()
        runner.replies["1"] = '<fishhome_delegate>[{"kind":"web","query":"查公开指南"}]</fishhome_delegate>'
        runner.delegate_kinds = ("web",)
        calls = []

        async def worker(method, ident, body=None):
            calls.append((method, ident, body))
            if method == "PUT":
                return {"id": ident, "state": "running"}
            if len(calls) == 1:
                return {"id": ident, "state": "not_found"}
            return {"id": ident, "state": "completed", "report": {
                "kind": "dsh_web_search", "summary": "完整来源", "billing": [{"source": "go"}],
                "evidence": [{"url": "https://docs.python.org/3/"}]}}

        runner.delegate_public = worker
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("指南讨论", PROFILES, "interactive", 1, 0, 0, "delegation")["id"]
            args = (lambda _: runner, lambda _: "workspace", lambda _: True, None)
            await meetings.dispatch_next(*args)
            row = meetings.get(ident)
            self.assertEqual([entry["name"] for entry in row["transcript"]], ["乙"])
            self.assertEqual(row["status"], "running")
            runner.replies["1"] = "我看了工具收集的证据，判断如下。"
            await meetings.dispatch_next(*args)
            row = meetings.get(ident)
            self.assertEqual(row["status"], "completed")
            entry = row["transcript"][-1]
            self.assertEqual((entry["name"], entry["go_receipt_count"]), ("甲", 2))
            put = [call for call in calls if call[0] == "PUT"]
            self.assertEqual(len(put), 1)
            self.assertEqual(entry["tool_sources"], ["public-job:" + put[0][1]])
            self.assertIn("https://docs.python.org/3/", runner.sent[-1][2])
            self.assertEqual(runner.sent[-1][0], "1")

    async def test_verified_members_continue_and_late_receipt_is_admitted_once_after_reopen(self):
        runner = MeetingRunner()
        runner.delayed_receipts.add("2")
        runner.errors.add("3")
        runner.replies["2"] = "我还没答应负责这件事。\n[fishhome:review]"
        profiles = [*PROFILES, {"id": "3" * 16, "name": "丙", "persona": "丙的视角", "version": 1}]
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                ident = meetings.create("验证迟到与断流", profiles, "interactive", 2, 0, 0, "late")["id"]
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
                row = meetings.get(ident)
                self.assertEqual(row["status"], "completed")
                self.assertEqual([entry["round"] for entry in row["transcript"] if entry["name"] == "甲"], [1, 2])
                self.assertNotIn("乙", [entry["name"] for entry in row["transcript"]])
                self.assertTrue(any(turn["name"] == "乙" and turn["status"] == "pending_confirmation"
                                    for turn in row["summary"]["unresolved_turns"]))
            runner.delayed_receipts.clear()
            with closing(core.Store(folder)) as store:
                meetings = DshMeetings(store, SCOPE)
                with patch("bili_activity_test.dsh_meetings.time", return_value=time() + 16) as clock:
                    for _ in range(2):
                        await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
                        clock.return_value += 16
                row = meetings.get(ident)
                recovered = [entry for entry in row["transcript"] if entry["name"] == "乙"]
                self.assertEqual(len(recovered), 1)
                self.assertTrue(recovered[0]["late"])
                self.assertEqual(recovered[0]["request_id"], runner.request_ids["2"])
                self.assertEqual(len([entry for entry in runner.sent if entry[0] == "2"]), 1)
                self.assertEqual(runner.overlap, 3)
                self.assertEqual(row["summary"]["conclusion_status"], "needs_host_review")
                self.assertEqual([notice["kind"] for notice in row["notices"]].count("late_review"), 1)

    async def test_casual_meeting_keeps_agenda_and_can_end_without_work_report(self):
        runner = MeetingRunner()
        runner.replies.update({"1": "我先听。\n[fishhome:listen]", "2": "[fishhome:leave]"})
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("随便聊聊", PROFILES, "interactive", 1, 0, 0, "casual", style="casual")["id"]
            result = meetings.post(ident, "别研究了，聊想吃的东西", "agenda", kind="agenda")
            self.assertEqual(result["post_receipt"]["visibility"], "next_prompt")
            await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
            row = meetings.get(ident)
            self.assertEqual(row["status"], "completed")
            self.assertFalse(row["notices"])
            self.assertIn("别研究了", runner.sent[0][2])
            self.assertNotIn("末轮用 JSON", runner.sent[0][2])
            result = meetings.post(ident, "主人看过了", "note")
            self.assertEqual(result["post_receipt"]["visibility"], "record_only")
            result = meetings.post(ident, "继续聊", "resume", kind="continue")
            self.assertEqual((result["status"], result["post_receipt"]["visibility"]), ("queued", "next_prompt"))

    async def test_casual_round_limit_is_optional_and_closes_after_the_requested_round(self):
        for limit in (0, 1):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
                runner = MeetingRunner()
                meetings = DshMeetings(store, SCOPE)
                ident = meetings.create("聊一聊", PROFILES, "background", limit, 0, 0,
                                         "source", style="casual", report_on_close=True)["id"]
                args = (lambda _: runner, lambda _: "workspace", lambda _: True, None)
                if limit:
                    runner.hold.add("2")
                    running = asyncio.create_task(meetings.dispatch_next(*args))
                    await asyncio.wait_for(runner.accepted.wait(), 2)
                    recorded = meetings.post(ident, "调整议程", "adjust", kind="agenda")
                    self.assertEqual(recorded["round_limit"], limit)
                    self.assertEqual(recorded["post_receipt"]["visibility"], "record_only")
                    runner.finish("2", "这一轮说完了")
                    runner.hold.remove("2")
                    await asyncio.wait_for(running, 4)
                else:
                    await meetings.dispatch_next(*args)
                row = meetings.get(ident)
                self.assertEqual(row["round_limit"], limit or None)
                self.assertEqual(row["status"], "completed" if limit else "running")
                if limit:
                    await meetings.dispatch_next(*args)
                    self.assertEqual(len(runner.sent), 2)
                    self.assertEqual([notice["kind"] for notice in row["notices"]], ["completed"])

    async def test_open_ended_discussion_waits_for_result_and_records_disagreement(self):
        runner = MeetingRunner()
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("讨论出可交付的决定", PROFILES, "background", 0, 0, 0, "open")["id"]
            args = (lambda _: runner, lambda _: "workspace", lambda _: True, None)
            solution = '{"suggested_decisions":["先保留两种方案"],"disagreements":["还无法合并"]}\n[fishhome:ready]'
            runner.replies.update({"1": solution, "2": "还需要对一条证据"})
            await meetings.dispatch_next(*args)
            self.assertEqual(meetings.get(ident)["status"], "running")
            self.assertIsNone(meetings.get(ident)["round_limit"])
            runner.replies["2"] = solution
            await meetings.dispatch_next(*args)
            row = meetings.get(ident)
            self.assertEqual((row["status"], row["round"], row["close_reason"]), ("completed", 2, "goal_ready"))
            self.assertEqual(row["summary"]["conclusions"][-1]["participant_record"]["disagreements"], ["还无法合并"])

    async def test_explicit_duration_stops_new_rounds_even_without_a_result(self):
        runner = MeetingRunner()
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            args = (lambda _: runner, lambda _: "workspace", lambda _: True, None)
            with patch("bili_activity_test.dsh_meetings.time", return_value=1000):
                ident = meetings.create("讨论", PROFILES, "background", 0, 0, 0, "timed",
                                         duration_minutes=1, report_on_close=True)["id"]
                await meetings.dispatch_next(*args)
            with patch("bili_activity_test.dsh_meetings.time", return_value=1061):
                await meetings.dispatch_next(*args)
            row = meetings.get(ident)
            self.assertEqual((row["status"], row["round"], row["close_reason"]), ("completed", 1, "time_limit"))
            self.assertEqual(len(runner.sent), 2)
            self.assertEqual([notice["kind"] for notice in row["notices"]], ["completed"])

    async def test_final_round_agenda_can_switch_to_casual_in_the_same_meeting(self):
        runner = MeetingRunner()
        runner.hold.add("2")
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("先研究", PROFILES, "interactive", 0, 0, 0, "final")["id"]
            in_flight = asyncio.create_task(meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None))
            await asyncio.wait_for(runner.accepted.wait(), 2)
            comment = meetings.post(ident, "顺带说一句", "comment")
            self.assertEqual(comment["post_receipt"]["visibility"], "next_prompt")
            changed = meetings.post(ident, "研究先放着，随便聊", "new-agenda", kind="agenda", style="casual")
            self.assertEqual((changed["id"], changed["post_receipt"]["round"]), (ident, 2))
            runner.finish("2", "原议题的第二位发言")
            runner.hold.remove("2")
            await asyncio.wait_for(in_flight, 4)
            self.assertEqual(meetings.get(ident)["status"], "running")
            runner.replies.update({"1": "[fishhome:listen]", "2": "[fishhome:listen]"})
            await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
            self.assertTrue(all("研究先放着，随便聊" in prompt and "这场是闲聊" in prompt
                                for _, _, prompt in runner.sent[2:]))
            self.assertEqual(meetings.get(ident)["status"], "completed")

    async def test_casual_member_sees_new_confirmed_speech_before_own_prompt(self):
        runner = MeetingRunner()
        runner.replies.update({"1": "我想吃脆薯片。", "2": "[fishhome:listen]"})
        first_confirmed = asyncio.Event()
        original_receipts = runner.go_receipts

        async def recall(client, member_id, topic, turn):
            if member_id == PROFILES[1]["id"]:
                await asyncio.wait_for(first_confirmed.wait(), 2)
            return ""

        async def receipt(session):
            result = await original_receipts(session)
            if session == "1" and result["receipts"]:
                first_confirmed.set()
            return result

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meetings = DshMeetings(store, SCOPE)
            ident = meetings.create("宵夜随便聊", PROFILES, "interactive", 1, 0, 0, "fresh", style="casual")["id"]
            with patch("bili_activity_test.dsh_meetings._memory_prepare", side_effect=recall), \
                    patch.object(runner, "go_receipts", side_effect=receipt):
                await meetings.dispatch_next(lambda _: runner, lambda _: "workspace", lambda _: True, None)
            self.assertIn("我想吃脆薯片。", next(text for session, _, text in runner.sent if session == "2"))
            self.assertEqual(meetings.get(ident)["round"], 1)

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
