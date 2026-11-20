import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_bili_activity import core, main
from bili_activity_test.commissioned import CommissionedFiles
from bili_activity_test.dsh_task import DshTaskFailure, execute_text_turn
from bili_activity_test.dsh_roster import DshRoster


SCOPE = "qq:FriendMessage:12345"
PLAN = {"cwd": r"C:\Runner\commission-files", "source": "sample.csv", "instruction": "Summarize the sample."}
FILE_REPORT = "sample.csv has two rows.\n" + "Checked both rows. " * 70 + "FILE-EOF"


class FileRunner:
    def __init__(self, task, preset="fishhome-files", report="sample.csv has two rows."):
        self.task, self.preset = task, preset
        self.report = report
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
            content = {self.task["report"]: self.report,
                       self.task["sources"]: '{"source":"sample.csv"}',
                       self.task["source"]: "a,b\n1,2\n"}
            return {"text": content[path], "eof": True, "absolutePath": str(PureWindowsPath(PLAN["cwd"]) / path)}
        if method == "session.projections":
            return {"values": {"tokenUsage": {"input": 50, "output": 20}}}
        raise AssertionError(method)

    async def close(self):
        pass


class CommissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_branches_preserve_source_and_control_only_the_selected_tree(self):
        from bili_activity_test.team_delegation import prepare, references

        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
                jobs = CommissionedFiles(store)
                owner = "12345"
                bot = object.__new__(main.BiliActivity)
                bot.store = store
                bot.context = SimpleNamespace()
                setattr(bot.context, main.COMMISSION_BRIDGE, lambda *args: PLAN["cwd"])
                bot.cfg = lambda: {"owner_scope": SCOPE, "owner_id": owner}
                bot.start_commission_worker = lambda *_: None
                spec = {"kind": "python", "source": "sample.csv", "instruction": "Calculate the count sum."}
                other = {"kind": "file", "source": "sample.csv", "instruction": "Read the column names."}
                parent = prepare([spec, other], actor="partner", request_id="turn-1", receipt_count=1,
                    parent={"ref": "chat:root", "autonomous": False})
                first, sibling = parent["children"]

                def dispatch(action, child, parent_job=parent):
                    return bot.local_tool_job(SCOPE, action, parent_job, child)

                invalid = prepare([{**spec, "source": "another.csv"}], actor="partner", request_id="turn-2", receipt_count=1,
                    parent={"ref": "task:parent", "source": "sample.csv", "shared": False, "compute": True, "autonomous": False})
                self.assertEqual(dispatch("PUT", invalid["children"][0], invalid)["state"], "failed")
                first.update(dispatch("PUT", first))
                self.assertEqual(dispatch("PUT", first)["reference"], first["reference"])
                sibling.update(dispatch("PUT", sibling))

                def row(child):
                    return store.get_setting(jobs._key(SCOPE, child["reference"].removeprefix("task:")))

                parent_row = row(first)
                blocked = {row(sibling)["id"]}
                runners = {}
                def make(_scope):
                    class LocalRunner(FileRunner):
                        async def call(self, method, args):
                            if method == "session.create":
                                self.calls.append((method, args))
                                return {"sessionId": self.task_id, "agentPreset": args["agentPreset"]}
                            if method == "workspaceFiles.read":
                                self.calls.append((method, args))
                                return {"text": "a,b\n1,2\n", "eof": True,
                                        "absolutePath": str(PureWindowsPath(PLAN["cwd"]) / args["path"])}
                            return await super().call(method, args)
                    runner = LocalRunner({})
                    runner.task_id = "test-" + str(len(runners))
                    runners[runner.task_id] = runner
                    runner.compute_enabled = True
                    runner.model_route = "go"
                    runner.go_receipts = AsyncMock(return_value={"receipts": [{"source": "go"}]})
                    async def delegate_local(action, job, child):
                        return dispatch(action, child, job)
                    runner.delegate_local = delegate_local
                    runner.delegate_kinds = ("file", "python")
                    return runner

                async def wait(_client, task, _control):
                    if task["id"] == parent_row["id"] and not task.get("tool_delegations"):
                        return '<fishhome_delegate>[{"kind":"file","source":"sample.csv","instruction":"Extract the count rows."}]</fishhome_delegate>'
                    return "From sample.csv: item and count, 2 and 3."

                send = AsyncMock()
                args = (SCOPE, owner, make, send, lambda task: task["id"] not in blocked)
                with patch("bili_activity_test.dsh_task._wait_for_turn", new=wait):
                    await jobs.dispatch_next(*args)
                    updated = row(first)
                    grandchild = updated["tool_delegations"][0]["children"][0]
                    self.assertEqual(row(grandchild)["delegation"]["parent"], first["reference"])
                    self.assertEqual(row(grandchild)["delegation"]["actor"], "partner")
                    self.assertNotIn("agent", row(grandchild))
                    self.assertEqual(runners[updated["session_id"]].calls[0][1]["agentPreset"], "fishhome-local-compute")
                    self.assertIn("a,b", str(runners[updated["session_id"]].calls))

                    self.assertTrue(jobs.request_running_control(SCOPE, owner, updated["id"], "pause", internal=True))
                    blocked.clear()
                    blocked.add(row(grandchild)["id"])
                    await jobs.dispatch_next(*args)
                    self.assertEqual(row(first)["state"], "paused")
                    self.assertEqual(row(grandchild)["state"], "paused")
                    self.assertEqual(row(sibling)["state"], "completed")
                    if cancel:
                        self.assertTrue(jobs.cancel(SCOPE, owner, updated["id"], internal=True))
                    else:
                        self.assertTrue(jobs.pause_or_resume(SCOPE, owner, updated["id"], resume=True, internal=True))
                    blocked.clear()
                    for _ in range(3):
                        await jobs.dispatch_next(*args)
                expected = "cancelled" if cancel else "completed"
                self.assertEqual(row(first)["state"], expected)
                self.assertEqual(row(grandchild)["state"], expected)
                self.assertEqual(row(sibling)["state"], "completed")
                self.assertEqual(references(row(first)["tool_delegations"]), [grandchild["reference"]])
                self.assertEqual(dispatch("GET", sibling)["report"]["evidence"][0]["source"], "sample.csv")
                send.assert_not_awaited()

    async def test_only_explicit_owner_go_commission_delivers_original_when_agent_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            original = FILE_REPORT + ("\nAdditional original detail." * 40)
            candidate = jobs.accept(SCOPE, "12345", "explicit", PLAN, require_go=True, wake_agent=True)
            implicit = jobs.accept(SCOPE, "12345", "author", PLAN, via_agent=True, require_go=True, wake_agent=True)
            unverified = jobs.accept(SCOPE, "12345", "unverified", PLAN, require_go=True, wake_agent=True)
            for ident in (candidate, implicit, unverified):
                key = jobs._key(SCOPE, ident)
                task = store.get_setting(key)
                task.update(state="completed", result=original, usage={"billing_source": "go",
                            "go_receipts": [] if ident == unverified else [{"source": "go"}]})
                store.setting(key, task)

            send = AsyncMock()
            wake = AsyncMock(side_effect=RuntimeError("Agent could not complete with a 402"))
            enqueue = AsyncMock(side_effect=[{"fishhome_queue_id": "first"}, {"fishhome_queue_id": "last"}])
            args = (SCOPE, "12345", None, send, lambda _task: True)
            await jobs.dispatch_next(*args, wake=wake, enqueue=enqueue, ready=lambda: False)
            queued = "".join(call.args[1] for call in enqueue.call_args_list)
            self.assertIn("肥鱼尚未复核", queued)
            self.assertTrue(queued.endswith(original))
            self.assertEqual(enqueue.await_count, 2)
            self.assertEqual(wake.await_count, 3)
            await jobs.dispatch_next(*args, wake=wake, enqueue=enqueue, ready=lambda: False)
            self.assertEqual(enqueue.await_count, 2)
            self.assertEqual(wake.await_count, 3)
            task = store.get_setting(jobs._key(SCOPE, candidate))
            self.assertEqual(task["delivery"], "queued")
            self.assertIn("original_delivery", task)
            self.assertNotIn("agent_consumed_at", task)
            for ident in (implicit, unverified):
                self.assertEqual(store.get_setting(jobs._key(SCOPE, ident))["delivery"], "pending")
            send.assert_not_awaited()

    async def test_named_file_resumes_from_tool_receipt_and_can_cancel_while_paused(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
                jobs = CommissionedFiles(store)
                roster = DshRoster(store, SCOPE)
                agent = roster.snapshot(roster.create("取证鱼", "仔细核对来源")['id'])
                ident = jobs.accept(SCOPE, "12345", "file", PLAN, agent=agent, require_go=True)
                key = jobs._key(SCOPE, ident)
                runner = FileRunner(store.get_setting(key))
                runner.model_route = "go"
                runner.delegate_kinds = ("web",)
                runner.go_receipts = AsyncMock(return_value={"receipts": [1]})
                work_calls = []

                async def worker(method, job_id, body=None):
                    work_calls.append(method)
                    if method == "DELETE":
                        return {"id": job_id, "state": "cancelled"}
                    if method == "PUT":
                        return {"id": job_id, "state": "running"}
                    if len(work_calls) == 1:
                        return {"id": job_id, "state": "not_found"}
                    return {"id": job_id, "state": "completed", "report": {
                        "kind": "dsh_web_search", "summary": "资料已核", "billing": [{"source": "go"}],
                        "evidence": [{"url": "https://docs.python.org/3/"}]}}

                runner.delegate_public = worker
                args = (SCOPE, "12345", lambda _scope: runner, AsyncMock(return_value={"message_id": "1"}),
                        lambda _task: True)
                wait = AsyncMock(side_effect=['<fishhome_delegate>[{"kind":"web","query":"查公开资料"}]</fishhome_delegate>',
                                              "文件已写好"])
                with patch("bili_activity_test.dsh_task._wait_for_turn", new=wait):
                    await jobs.dispatch_next(*args)
                    self.assertEqual(store.get_setting(key)["state"], "submitted")
                    self.assertTrue(jobs.request_running_control(SCOPE, "12345", ident, "pause"))
                    await jobs.dispatch_next(*args)
                    self.assertEqual(store.get_setting(key)["state"], "paused")
                    self.assertEqual(work_calls, ["GET", "PUT"])
                    if cancel:
                        self.assertTrue(jobs.cancel(SCOPE, "12345", ident))
                    else:
                        self.assertTrue(jobs.pause_or_resume(SCOPE, "12345", ident, resume=True))
                    await jobs.dispatch_next(*args)
                row = store.get_setting(key)
                self.assertEqual(row["state"], "cancelled" if cancel else "completed")
                self.assertEqual(work_calls[-1], "DELETE" if cancel else "GET")
                prompts = [args for method, args in runner.calls if method == "session.prompt"]
                self.assertEqual(len(prompts), 1 if cancel else 2)
                if not cancel:
                    self.assertEqual(prompts[0]["sessionId"], prompts[1]["sessionId"])
                    self.assertIn("https://docs.python.org/3/", prompts[1]["content"][0]["text"])

    async def test_scheduler_runs_independent_files_but_orders_shared_writers(self):
        started = []

        async def execute(_client, task, _save, may_submit, _control):
            self.assertTrue(may_submit())
            started.append(task["source_message_id"])
            await asyncio.sleep(0)
            return "done", {}

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            shared = {"cwd": r"D:\FishHome\workspaces\shared", "source": None,
                      "instruction": "Edit the shared draft.", "shared": True, "agent_selected": True}
            for ident, plan in (("a", shared), ("b", shared), ("c", PLAN), ("d", PLAN)):
                jobs.accept(SCOPE, "12345", ident, plan)
            client = AsyncMock()
            args = (SCOPE, "12345", lambda _scope: client, AsyncMock(return_value={"message_id": "1"}),
                    lambda _task: True)
            with patch("bili_activity_test.commissioned.execute_file_task", new=execute):
                await jobs.dispatch_next(*args)
                self.assertCountEqual(started, ["a", "c", "d"])
                await jobs.dispatch_next(*args)
                self.assertEqual(started[-1], "b")

    async def test_public_display_limit_does_not_limit_dispatch(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            for ident in range(12):
                jobs.accept_public(SCOPE, "12345", f"web-{ident}", {"kind": "web", "query": f"query {ident}"})
            self.assertEqual(len(jobs.list(SCOPE, "12345", job_kind="public")), 10)
            call = AsyncMock(side_effect=lambda method, ident, body: {"id": ident, "state": "running"})
            await jobs.dispatch_public(SCOPE, "12345", call, None, None, lambda _: True,
                                       ready=lambda: True, wake=None)
            self.assertEqual(call.await_count, 12)

    async def test_long_public_reply_queues_every_part_and_waits_for_all_receipts(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            accepted = jobs.accept_public(SCOPE, '12345', 'search-long', {'kind': 'web', 'query': 'Python pathlib'})
            report = '长报告。' * 700 + 'WEB-EOF'
            call = AsyncMock(side_effect=lambda _, ident, body: {
                'id': ident, 'state': 'completed', 'report': {'summary': report}})
            enqueue = AsyncMock(side_effect=[{'fishhome_queue_id': 'first'}, {'fishhome_queue_id': 'last'}])
            await jobs.dispatch_public(SCOPE, '12345', call, lambda task, result: (result['summary'], {}),
                                       AsyncMock(), lambda _: True, ready=lambda: False,
                                       wake=AsyncMock(return_value=report), enqueue=enqueue)
            self.assertEqual(''.join(q.args[1] for q in enqueue.await_args_list), report)
            self.assertTrue(all(len(q.args[1]) <= 2000 for q in enqueue.await_args_list))
            self.assertEqual(len({q.args[2] for q in enqueue.await_args_list}), 2)
            key = jobs._key(SCOPE, accepted['id'])
            receipts = {'first': {'state': 'sent', 'message_id': '1'}, 'last': {'state': 'pending'}}
            jobs.settle_outbox(SCOPE, receipts.get)
            self.assertEqual(store.get_setting(key)['delivery'], 'queued')
            receipts['last'] = {'state': 'sent', 'message_id': '2'}
            jobs.settle_outbox(SCOPE, receipts.get)
            self.assertEqual(store.get_setting(key)['delivery'], 'sent')
            self.assertEqual(store.get_setting(key)['message_ids'], ['1', '2'])

    async def test_offline_reply_does_not_hold_up_the_next_public_completion(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            for ident, query in (("search-1", "Python pathlib"), ("search-2", "Python asyncio")):
                jobs.accept_public(SCOPE, "12345", ident, {"kind": "web", "query": query})
            call = AsyncMock(side_effect=lambda method, ident, body: {"id": ident, "state": "completed",
                                                                     "report": {"summary": body["query"]}})
            record = lambda task, report: (report["summary"], {})
            wake = AsyncMock(side_effect=lambda task: task["result"])
            send = AsyncMock(return_value={"message_id": "42"})
            for _ in range(2):
                await jobs.dispatch_public(SCOPE, "12345", call, record, send, lambda _: True,
                                           ready=lambda: False, wake=wake)
            self.assertEqual([item.args[0]["result"] for item in wake.await_args_list],
                             ["Python pathlib", "Python asyncio"])
            send.assert_not_awaited()
            for _ in range(2):
                await jobs.dispatch_public(SCOPE, "12345", call, record, send, lambda _: True,
                                           ready=lambda: True, wake=wake)
            self.assertEqual([item.args[1] for item in send.await_args_list],
                             ["Python pathlib", "Python asyncio"])
            self.assertEqual(call.await_count, 2)
            self.assertEqual(wake.await_count, 2)

    async def test_named_chat_reuses_session_and_versions_persona(self):
        class ChatRunner(FileRunner):
            model_route = "go"
            billed = 0

            async def call(self, method, args):
                if method == "session.prompt":
                    self.task["request_id"] = args["requestId"]
                    self.billed += 1
                if method == "session.history":
                    events = (await super().call(method, args))["events"]
                    events.insert(1, {"type": "assistant/message", "data": {
                        "message": {"content": [{"type": "text", "text": "Reply."}]}}})
                    return {"events": events}
                return await super().call(method, args)

            async def go_receipts(self, session):
                return {"receipts": list(range(self.billed))}

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            worker = roster.create("First name", "First persona.")
            runner = ChatRunner({}, preset="fishhome-chat")
            first = await roster.chat(worker["id"], runner, PLAN["cwd"], "First message.")
            roster.update(worker["id"], name="Renamed", persona="Second persona.")
            resumed = DshRoster(store, SCOPE)
            second = await resumed.chat(worker["id"], runner, PLAN["cwd"], "Second message.")
            self.assertEqual((first["session_id"], second["session_id"]), ("job-1", "job-1"))
            self.assertEqual((first["persona_version"], second["persona_version"]), (1, 2))
            self.assertEqual((first["go_receipt_count"], second["go_receipt_count"]), (1, 1))
            self.assertEqual([method for method, _ in runner.calls].count("session.create"), 1)
            self.assertEqual(len(resumed.get(worker["id"])["profiles"]), 2)
            with self.assertRaises(ValueError):
                DshRoster(store, "unrelated").get(worker["id"])

    async def test_text_requires_body_of_the_current_turn(self):
        class TextRunner(FileRunner):
            model_route = "go"
            current_reply = ""

            async def call(self, method, args):
                if method == "session.prompt":
                    self.task["request_id"] = args["requestId"]
                if method == "session.history":
                    def assistant(text):
                        return {"type": "assistant/message", "data": {
                            "message": {"content": [{"type": "text", "text": text}]}}}
                    history = (await super().call(method, args))["events"]
                    return {"events": [assistant("stale reply"), history[0],
                                       assistant(self.current_reply), history[1]]}
                return await super().call(method, args)

            async def go_receipts(self, session):
                return {"receipts": ["go-call"], "needs_review": False}

        runner = TextRunner({}, preset="fishhome-chat")
        with self.assertRaisesRegex(DshTaskFailure, "没有返回正文"):
            await execute_text_turn(runner, PLAN["cwd"], "Question")
        runner.current_reply = "Specific answer."
        result = await execute_text_turn(runner, PLAN["cwd"], "Question")
        self.assertEqual((result["result"], result["go_receipt_count"]), ("Specific answer.", 1))
        self.assertNotIn("workspaceFiles.read", [name for name, _ in runner.calls])

    async def test_background_reply_persists_agent_result_when_qq_is_disconnected(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            ident = jobs.accept(SCOPE, "12345", "wake", PLAN, via_agent=True, wake_agent=True)
            runner = FileRunner(jobs.list(SCOPE, "12345", ident=ident)[0])
            wake = AsyncMock(return_value="我拿到小弟的结果了：有两行。")
            send = AsyncMock(return_value={"message_id": "42"})
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True,
                                     ready=lambda: False, wake=wake)
            row = jobs.list(SCOPE, "12345", ident=ident)[0]
            self.assertEqual((row["state"], row["delivery"]), ("completed", "pending"))
            self.assertIn("agent_consumed_at", row)
            wake.assert_awaited_once()
            send.assert_not_awaited()
            jobs.recover()
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True, wake=wake)
            self.assertEqual(jobs.list(SCOPE, "12345", ident=ident)[0]["delivery"], "sent")
            wake.assert_awaited_once()
            send.assert_awaited_once_with(SCOPE, "我拿到小弟的结果了：有两行。")
            self.assertEqual([name for name, _ in runner.calls].count("session.prompt"), 1)

    async def test_shared_file_uses_workspace_relative_path_and_keeps_source_label(self):
        class SharedRunner(FileRunner):
            async def call(self, method, args):
                if method != "workspaceFiles.read":
                    return await super().call(method, args)
                self.calls.append((method, args))
                path = args["path"]
                content = {"sample.csv": "item,count\npen,3", self.task["report"]: "Source: shared/sample.csv\nTotal 3.",
                           self.task["sources"]: '{"source":"shared/sample.csv"}'}
                return {"text": content[path], "eof": True,
                        "absolutePath": str(PureWindowsPath(self.task["cwd"]) / path)}

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            jobs.accept(SCOPE, "12345", "shared", {**PLAN, "cwd": r"C:\Owner\shared", "source": "shared/sample.csv"})
            runner = SharedRunner(jobs.list(SCOPE, "12345")[0])
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, AsyncMock(return_value={"message_id": "42"}), lambda _: True)
            job = jobs.list(SCOPE, "12345")[0]
            self.assertEqual((job["state"], job["source"]), ("completed", "shared/sample.csv"))
            self.assertEqual([args["path"] for method, args in runner.calls if method == "workspaceFiles.read"].count("sample.csv"), 2)

    async def test_source_read_failure_keeps_native_session_and_does_not_submit_prompt(self):
        class BlockedRunner(FileRunner):
            async def call(self, method, args):
                if method == "workspaceFiles.read":
                    raise PermissionError("Windows refused workspace source")
                return await super().call(method, args)

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            jobs.accept(SCOPE, "12345", "blocked", {**PLAN, "cwd": r"C:\Owner\shared",
                        "source": "shared/qq-zip/task-x/001-tide.csv"})
            runner = BlockedRunner(jobs.list(SCOPE, "12345")[0])
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner,
                                     AsyncMock(return_value={"message_id": "42"}), lambda _: True)
            job = jobs.list(SCOPE, "12345")[0]
            self.assertEqual(job["failure"]["code"], "source_read")
            self.assertTrue(job["session_id"])
            self.assertNotIn("session.prompt", [method for method, _ in runner.calls])

    async def test_agent_job_returns_inline_or_sends_one_natural_receipt_after_timeout(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            send = AsyncMock(return_value={"message_id": "42"})
            runs = ["fast", "slow"]
            with patch("bili_activity_test.commissioned.time", return_value=100) as clock:
                for message_id in runs:
                    ident = jobs.accept(SCOPE, "12345", message_id, PLAN, via_agent=True, inline_seconds=15)
                    self.assertEqual(jobs.list(SCOPE, "12345", explicit_only=True), [])
                    runner = FileRunner(jobs.list(SCOPE, "12345", ident=ident)[0])
                    await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
                    send.assert_not_awaited()
                    if message_id == "fast":
                        result = jobs.inline_result(SCOPE, ident)
                        self.assertEqual((result["status"], result["source"]), ("completed", "sample.csv"))
                    else:
                        clock.return_value = 120
                        self.assertIsNone(jobs.inline_result(SCOPE, ident))
                        await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
                        await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
                        send.assert_awaited_once()
                        self.assertNotIn(ident, send.await_args.args[1])
                        self.assertIn("sample.csv has two rows", send.await_args.args[1])
                    self.assertEqual(sum(method == "session.prompt" for method, _ in runner.calls), 1)

    async def test_new_go_jobs_do_not_call_official_deepseek_if_go_is_disabled(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            ident = jobs.accept(SCOPE, "12345", "1", PLAN, require_go=True)
            runner = FileRunner(jobs.list(SCOPE, "12345", ident=ident)[0])
            send = AsyncMock()
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
            self.assertEqual(runner.calls, [])
            self.assertEqual(jobs.list(SCOPE, "12345", ident=ident)[0]["state"], "prepared")
            send.assert_not_awaited()

    async def test_running_pause_waits_for_confirmation_and_resumes_same_session(self):
        class InterruptibleRunner(FileRunner):
            async def call(self, method, args):
                if method == "session.state":
                    return {"running": ("session.cancel", args) not in self.calls}
                if method == "session.history":
                    events = (await super().call(method, args))["events"]
                    if not any(name == "session.cancel" for name, _ in self.calls):
                        return {"events": events[:1]}
                    events[1]["data"]["reason"]["kind"] = "interrupted"
                    return {"events": events}
                if method == "session.cancel":
                    self.calls.append((method, args))
                    return {}
                return await super().call(method, args)

        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            ident = jobs.accept(SCOPE, "12345", "1", PLAN)
            task = jobs.list(SCOPE, "12345")[0]
            task.update(state="submitted", session_id="job-1")
            store.setting(jobs._key(SCOPE, ident), task)
            self.assertTrue(jobs.request_running_control(SCOPE, "12345", ident, "pause"))
            self.assertEqual(jobs.list(SCOPE, "12345")[0]["state"], "submitted")
            interrupted = InterruptibleRunner(task)
            send = AsyncMock(return_value={"message_id": "42"})
            with patch("bili_activity_test.dsh_task.asyncio.sleep", new_callable=AsyncMock):
                await jobs.dispatch_next(SCOPE, "12345", lambda _: interrupted, send, lambda _: True)
            self.assertEqual(interrupted.calls.count(("session.cancel", {"sessionId": "job-1"})), 1)
            self.assertEqual(jobs.list(SCOPE, "12345")[0]["state"], "paused")
            send.assert_not_awaited()

            self.assertTrue(jobs.pause_or_resume(SCOPE, "12345", ident, resume=True))
            continuing = jobs.list(SCOPE, "12345")[0]
            self.assertNotEqual(continuing["request_id"], task["request_id"])
            runner = FileRunner(continuing)
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)
            self.assertNotIn("session.create", [method for method, _ in runner.calls])
            self.assertEqual(runner.calls[0][1]["sessionId"], "job-1")
            self.assertEqual(jobs.list(SCOPE, "12345")[0]["state"], "completed")
            send.assert_awaited_once()

    async def test_manual_pause_requires_owner_resume_then_preserves_queue(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            ident = jobs.accept(SCOPE, "12345", "1", PLAN)
            self.assertFalse(jobs.pause_or_resume(SCOPE, "other", ident))
            self.assertTrue(jobs.pause_or_resume(SCOPE, "12345", ident))
            self.assertEqual(jobs.list(SCOPE, "12345", pending_only=True), [])
            self.assertTrue(jobs.pause_or_resume(SCOPE, "12345", ident, resume=True))
            runner = FileRunner(jobs.list(SCOPE, "12345")[0])
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner,
                                     AsyncMock(return_value={"message_id": "42"}), lambda _: True)
            self.assertEqual(jobs.list(SCOPE, "12345")[0]["state"], "completed")

    async def test_compute_is_opt_in_and_preserves_report_and_usage(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            jobs = CommissionedFiles(store)
            jobs.accept(SCOPE, "12345", "1", PLAN)
            task = jobs.list(SCOPE, "12345")[0]
            runner = FileRunner(task, preset="fishhome-compute", report=FILE_REPORT)
            runner.compute_enabled = True
            send = AsyncMock(return_value={"message_id": "42"})
            await jobs.dispatch_next(SCOPE, "12345", lambda _: runner, send, lambda _: True)

            self.assertEqual(runner.calls[0][1]["agentPreset"], "fishhome-compute")
            prompt = next(args for method, args in runner.calls if method == "session.prompt")
            self.assertIn("source_text", prompt["content"][0]["text"])
            row = jobs.list(SCOPE, "12345")[0]
            self.assertEqual((row["state"], row["billing"]), ("completed", "interaction"))
            self.assertEqual(row["usage"], {"input": 50, "output": 20})
            self.assertEqual(row["result"], FILE_REPORT)
            self.assertIn(FILE_REPORT, send.call_args.args[1])

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
