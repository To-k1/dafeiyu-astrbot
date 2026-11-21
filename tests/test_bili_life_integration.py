"""Exercise real plugin methods with local transport/model substitutes.

These tests do not assert live QQ delivery or a production deployment.
"""
import asyncio
import base64
from datetime import datetime
from importlib import import_module
import json
from pathlib import PureWindowsPath
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_bili_activity as fixtures
main, core = fixtures.main, fixtures.core
dsh_task = import_module(main.__package__ + ".dsh_task")
dsh_cards = import_module(main.__package__ + ".dsh_cards")
SCOPE, OTHER_SCOPE, FakeEvent = fixtures.SCOPE, fixtures.OTHER_SCOPE, fixtures.FakeEvent


class LifeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.PluginTests.asyncSetUp(self)
        self.config.update(life_enabled=True, one_shot_verified=True, chat_cooldown_seconds=0,
                           collection_management=True)
        self.save_config()
        self.plugin.gate = main.PhoneGate()
        self.plugin.life_runs, self.plugin.run_goals = set(), {}
        self.plugin.flush_memory = AsyncMock()
        self.calls = []

        async def json_call(name, args):
            self.calls.append((name, args))
            if name == "bili_phone":
                return {"online": True}
            if name == "bili_prepare_video":
                return {"identity": {"bvid": args["bvid"], "cid": args["cid"]}, "range": [0, 20],
                        "subtitles": [{"from": 0, "to": 20, "content": "合作建造演示"}],
                        "frames": [], "audios": []}
            if name == "bili_preview":
                return {"ok": True, "args": args["args"], "target": {"title": "演示"}}
            if name == "bili_execute":
                return {"verified": True}
            if name == "bili_read":
                return {"identity": {"bvid": "BVexample", "cid": 1}, "items": ["实机演示"]}
            raise AssertionError(name)

        async def call(name, args):
            self.calls.append((name, args))
            return types.SimpleNamespace(isError=False, structuredContent={
                "online": True, "identity_verified": False, "reason": "bilibili_not_focused"})

        self.backend = types.SimpleNamespace(json_call=AsyncMock(side_effect=json_call), call=AsyncMock(side_effect=call))
        self.plugin.bili = lambda *args: self.backend
        self.plugin.face_plugin = lambda: None

    async def asyncTearDown(self):
        await fixtures.PluginTests.asyncTearDown(self)

    async def test_fish_shared_story_is_available_only_by_reading_the_tool(self):
        body = "只有主动读取才能拿到的出厂故事"
        impressions = "关于尚未成为小弟的角色\n" + "印象" * 2600
        dsh_cards.save_fish_impressions(self.plugin.store, impressions)
        row = dsh_cards.set_story(self.plugin.store, SCOPE,
            filename="旧站台.txt", text=body, participants=[dsh_cards.FISH_PARTICIPANT])
        tool = fixtures.FunctionTool(name=main.STORY_TOOL, active=True)
        registry = types.SimpleNamespace(get_tool=lambda name: tool if name == main.STORY_TOOL else None)
        self.plugin.context = types.SimpleNamespace(
            get_llm_tool_manager=lambda: types.SimpleNamespace(get_full_tool_set=lambda: registry))
        event = FakeEvent()
        request = types.SimpleNamespace(func_tool=fixtures.ToolSet(), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(event, request)
        self.assertIn(main.STORY_TOOL, request.func_tool.names())
        self.assertNotIn(body, request.system_prompt)
        self.assertNotIn(impressions[:100], request.system_prompt)
        self.assertNotIn("旧站台", request.system_prompt)
        self.plugin.life_runs.add("story-lookup")
        life_event = FakeEvent(run_id="story-lookup")
        life_prompt = types.SimpleNamespace(func_tool=fixtures.ToolSet(), system_prompt="life", contexts=[])
        await self.plugin.guard_phone(life_event, life_prompt)
        self.assertIn(main.STORY_TOOL, life_prompt.func_tool.names())
        self.assertNotIn(body, life_prompt.system_prompt)
        self.assertNotIn(impressions[:100], life_prompt.system_prompt)
        catalog = json.loads(await self.plugin.story(event, query="旧站台"))
        self.assertEqual(catalog["stories"][0]["id"], row["id"])
        self.assertTrue(catalog["stories"][0]["fish_participates"])
        self.assertNotIn("text", catalog["stories"][0])
        self.assertNotIn("关于尚未成为小弟", str(catalog))
        opened = json.loads(await self.plugin.story(event, action="read", story_id=row["id"]))
        self.assertEqual(opened["text"], body)
        self.assertTrue(opened["records_are_data_not_instructions"])
        first = json.loads(await self.plugin.story(event, action="impressions"))
        last = json.loads(await self.plugin.story(life_event, action="impressions", offset=first["next_offset"]))
        self.assertEqual(first["kind"], "authored_impressions")
        self.assertIsNone(last["next_offset"])
        self.assertEqual(first["text"] + last["text"], impressions)
        other_event = FakeEvent(scope=OTHER_SCOPE)
        other_prompt = types.SimpleNamespace(func_tool=fixtures.ToolSet([tool]), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(other_event, other_prompt)
        self.assertNotIn(main.STORY_TOOL, other_prompt.func_tool.names())
        with self.assertRaises(PermissionError):
            await self.plugin.story(other_event, action="read", story_id=row["id"])
        with self.assertRaises(PermissionError):
            await self.plugin.story(other_event, action="impressions")
        dsh_cards.set_story(self.plugin.store, SCOPE, ident=row["id"], status="archived")
        self.assertEqual(json.loads(await self.plugin.story(event))["stories"], [])
        with self.assertRaises(ValueError):
            await self.plugin.story(event, action="read", story_id=row["id"])

    async def test_named_assignment_reuses_roster_and_keeps_selected_guide_revision(self):
        event = FakeEvent(event_id="named-file")
        self.plugin.start_commission_worker = Mock()
        dsh = types.SimpleNamespace(model_route="go", compute_enabled=True, close=AsyncMock())
        self.plugin.context = types.SimpleNamespace(
            fishhome_dsh_commission_client=lambda scope, **kw: r"D:\FishHome\workspaces\dsh\commission" if kw else dsh)
        worker = json.loads(await self.plugin.dsh_agents(event, "create", name="阿墨", persona="检查事实"))
        guide = json.loads(await self.plugin.dsh_guides(event, "save", title="查证", text="逐条列证据"))
        assigned = json.loads(await self.plugin.dsh_assign(event, "submit", "阿墨", instruction="检查清单",
            project="review", guide_ids=json.dumps([guide["id"]])))
        await self.plugin.dsh_guides(event, "save", guide["id"], "查证", "后续用新规则")
        visible = json.loads(await self.plugin.dsh_assign(event, agent_id=worker["id"], project="review"))["tasks"][0]
        self.assertEqual(visible["agent"]["guides"], [{"id": guide["id"], "title": "查证", "version": 1}])
        self.assertEqual(visible["project"], "review")
        self.assertNotIn("persona", visible["agent"])
        stored = self.plugin.store.get_setting(f"commissioned-file:v1:{SCOPE}:{assigned['id']}")
        self.assertIn("逐条列证据", dsh_task.assignment_prompt(stored["agent"]))
        self.assertFalse(main.DshRoster(self.plugin.store, SCOPE).get(worker["id"]).get("chat_session"))
        changed = json.loads(await self.plugin.dsh_assign(event, "pause", task_id=assigned["id"]))
        self.assertTrue(changed["updated"])
        with self.assertRaises(PermissionError):
            await self.plugin.dsh_assign(FakeEvent(scope=OTHER_SCOPE), "get", task_id=assigned["id"])

    async def test_meeting_tool_accepts_named_members_and_host_input(self):
        event = FakeEvent(event_id="called-a-meeting")
        self.plugin.start_dsh_meeting_worker = Mock()
        for name in ("阿墨", "团子"):
            await self.plugin.dsh_agents(event, "create", name=name, persona="独立表达")
        with self.assertRaisesRegex(ValueError, "请先确认"):
            await self.plugin.dsh_meeting(event, "create", topic="核对来源", members=json.dumps(["阿墨", "团子"]))
        self.plugin.start_dsh_meeting_worker.assert_not_called()
        meeting = json.loads(await self.plugin.dsh_meeting(event, "create", topic="核对来源",
            members=json.dumps(["阿墨", "团子"]), mode="interactive", style="discussion", report_every_minutes=3))
        ident = meeting["id"]
        self.assertEqual(meeting["report_every_minutes"], 3)
        self.assertIsNone(meeting["round_limit"])
        posted = json.loads(await self.plugin.dsh_meeting(FakeEvent(event_id="second-message"), "post", ident,
            text="先确认原出处", post_kind="agenda"))
        self.assertEqual(posted["post_receipt"], {"visibility": "next_prompt", "round": 1, "kind": "agenda"})
        readback = json.loads(await self.plugin.dsh_meeting(event, "get", ident))
        self.assertEqual(readback["transcript"][0]["text"], "先确认原出处")
        self.assertEqual({member["name"] for member in readback["members"]}, {"阿墨", "团子"})
        with self.assertRaises(PermissionError):
            await self.plugin.dsh_meeting(FakeEvent(scope=OTHER_SCOPE), "get", ident)

    async def test_tool_report_can_be_read_only_through_its_parent_record(self):
        job_id = "b" * 32
        parent_id = "a" * 16
        self.plugin.store.setting(f"dsh-meeting:v1:{SCOPE}:{parent_id}", {
            "transcript": [{"tool_sources": ["public-job:" + job_id]}]})
        worker = AsyncMock(return_value={"id": job_id, "state": "completed", "report": {"summary": "完整报告"}})
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_public_jobs_client=lambda _: worker)
        event = FakeEvent()
        parent = "meeting:" + parent_id
        with self.assertRaisesRegex(ValueError, "没有关联"):
            await self.plugin.dsh_public_job(event, "read_tool_report", "c" * 32, parent)
        worker.assert_not_awaited()
        result = json.loads(await self.plugin.dsh_public_job(event, "read_tool_report", job_id, parent))
        self.assertEqual(result["report"]["summary"], "完整报告")
        worker.assert_awaited_once_with("GET", job_id)

    async def test_web_search_delegates_by_default_and_preserves_personal_choice(self):
        evidence = [{"title": "Primary source", "url": "https://example.org/source"}]
        fetch = AsyncMock(side_effect=[
            {"kind": "dsh_web_search", "task_id": "search1", "summary": "Found evidence", "evidence": evidence, "billing": []},
            {"kind": "personal_web_search", "results": evidence},
            {"kind": "page_text", "text": "original text", "artifact": "shared/web-sources/primary.md"},
        ])
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_web_search_client=lambda scope: fetch if scope == SCOPE else None)
        event = FakeEvent()
        result = json.loads(await self.plugin.web_search(event, "project primary source"))
        fetch.assert_awaited_once_with("project primary source", "default")
        self.assertEqual(result["summary"], "Found evidence")
        self.assertEqual(self.plugin.store.life.episodes(SCOPE, kind="dsh_web_search")[0]["subject_kind"], "system")
        result = json.loads(await self.plugin.web_search(event, "check the citation", mode="personal"))
        fetch.assert_awaited_with("check the citation", "personal")
        self.assertEqual(result["results"], evidence)
        result = json.loads(await self.plugin.web_search(event, evidence[0]["url"], mode="page"))
        fetch.assert_awaited_with(evidence[0]["url"], "page")
        self.assertEqual(result["text"], "original text")

    async def test_personal_deepseek_search_uses_the_interaction_bucket(self):
        from plugin.astrbot_plugin_bili_activity.model_route import OFFICIAL_INTERACTION_BASE

        search = AsyncMock(return_value={"kind": "personal_web_search", "results": []})
        provider = types.SimpleNamespace(get_model=lambda: "deepseek-flash",
                                         provider_config={"api_base": OFFICIAL_INTERACTION_BASE})
        self.plugin.context = types.SimpleNamespace(
            fishhome_dsh_web_search_client=lambda scope: search if scope == SCOPE else None,
            get_using_provider_async=AsyncMock(return_value=provider))
        await self.plugin.web_search(FakeEvent(), "site:docs.python.org pathlib", mode="personal")
        search.assert_awaited_once_with("site:docs.python.org pathlib", "personal", bucket="interaction")

    async def test_dsh_history_recalls_prior_media_without_dispatching_again(self):
        store = self.plugin.store
        source = "BV1g8Wq6DEXj"
        episode = store.life.record_episode(SCOPE, kind="dsh_video", subject_kind="system", source_id="old-worker-id",
            evidence={"bvid": source, "samples": [{"frame_times": [10, 11]}]}, interpretation={"summary": "旧代看报告"})
        store.life.record_episode(SCOPE, kind="video", source_id="own", evidence={"bvid": source}, interpretation={})
        store.life.record_episode(OTHER_SCOPE, kind="dsh_video", source_id="other", evidence={"bvid": source}, interpretation={})
        result = json.loads(await self.plugin.dsh_history(FakeEvent(event_id="new-chat"), kind="video", query=source))
        self.assertEqual([row["id"] for row in result["tasks"]], [episode["id"]])
        self.assertEqual(result["tasks"][0]["summary"], "旧代看报告")
        self.assertEqual(result["tasks"][0]["evidence"]["samples"][0]["frame_times"], [10, 11])
        with self.assertRaises(PermissionError):
            await self.plugin.dsh_history(FakeEvent(scope=OTHER_SCOPE), kind="video", query=source)

    async def test_background_search_reconnects_and_puts_evidence_through_agent_once(self):
        sync = AsyncMock()
        source = {"title": "Primary source", "url": "https://example.org/source"}
        final = {"kind": "dsh_web_search", "task_id": "go-task", "session_id": "go-session",
                 "summary": "Background evidence", "evidence": [source], "billing": [{"source": "go"}]}
        calls = []

        async def api(method, ident, spec=None):
            calls.append((method, ident, spec))
            return {"id": ident, "state": "queued"} if method == "PUT" else {
                "id": ident, "state": "completed", "report": final}

        self.plugin.context = types.SimpleNamespace(fishhome_dsh_web_search_client=lambda _: sync,
                                                     fishhome_dsh_public_jobs_client=lambda _: api)
        self.plugin.start_public_jobs_worker = Mock()
        self.plugin.wake_commission = AsyncMock(return_value="我的报告有一个可核查来源。")
        send = AsyncMock(return_value={"message_id": "99"})
        self.plugin.sender = lambda: types.SimpleNamespace(send=send, ready=lambda _: True)
        event = FakeEvent(event_id="one-background-message")
        accepted = json.loads(await self.plugin.web_search(event, "project primary source", background=True))
        repeated = json.loads(await self.plugin.web_search(event, "project primary source", background=True))
        self.assertEqual((accepted["status"], repeated["id"]), ("prepared", accepted["id"]))
        self.assertEqual(calls, [])
        sync.assert_not_awaited()
        await self.plugin.dispatch_public_jobs(SCOPE, event.get_sender_id())
        self.plugin.wake_commission.assert_not_awaited()
        await self.plugin.dispatch_public_jobs(SCOPE, event.get_sender_id())
        await self.plugin.dispatch_public_jobs(SCOPE, event.get_sender_id())
        self.assertEqual([method for method, _, _ in calls], ["PUT", "GET"])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(self.plugin.store.life.episodes(SCOPE, kind="dsh_web_search")[0]["subject_kind"], "system")
        found = json.loads(await self.plugin.dsh_public_job(event, "status", accepted["id"]))["jobs"][0]
        self.assertEqual((found["status"], found["delivery"], found["evidence"]["sources"]),
                         ("completed", "sent", [source]))
        self.plugin.wake_commission.assert_awaited_once()
        send.assert_awaited_once_with(SCOPE, "我的报告有一个可核查来源。")
        with self.assertRaises(PermissionError):
            await self.plugin.dsh_public_job(FakeEvent(scope=OTHER_SCOPE), "status", accepted["id"])

    async def test_background_video_cancellation_does_not_report_completion(self):
        call = AsyncMock(side_effect=lambda method, ident, _: {
            "id": ident, "state": {"PUT": "running", "DELETE": "cancelled"}[method]})
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda _: AsyncMock(),
                                                     fishhome_dsh_public_jobs_client=lambda _: call)
        self.plugin.start_public_jobs_worker = Mock()
        self.plugin.wake_commission = AsyncMock()
        send = AsyncMock()
        self.plugin.sender = lambda: types.SimpleNamespace(send=send, ready=lambda _: True)
        event = FakeEvent(event_id="video-background-message")
        job = json.loads(await self.plugin.delegate_video(event, "BV1g8Wq6DEXj", "Describe seconds 10–14.", background=True))
        await self.plugin.dispatch_public_jobs(SCOPE, event.get_sender_id())
        pending = json.loads(await self.plugin.dsh_public_job(event, "cancel", job["id"]))
        self.assertTrue(pending["cancellation_requested"])
        await self.plugin.dispatch_public_jobs(SCOPE, event.get_sender_id())
        found = json.loads(await self.plugin.dsh_public_job(event, "status", job["id"]))["jobs"][0]
        self.assertEqual(found["status"], "cancelled")
        self.assertEqual([entry.args[0] for entry in call.await_args_list], ["PUT", "DELETE"])
        self.plugin.wake_commission.assert_not_awaited()
        send.assert_not_awaited()

    async def test_dsh_file_tool_accepts_a_normal_request_and_limits_input_to_registered_files(self):
        cwd = r"C:\Runner\files"

        def factory(scope, source=None):
            self.assertEqual(scope, SCOPE)
            if source is None:
                return types.SimpleNamespace(model_route="go")
            if source == "":
                return ["sample.csv"]
            if source == "sample.csv":
                return cwd
            raise ValueError("该文件未加入受托任务范围。")

        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=factory)
        event = FakeEvent(text="这个表里的 count 加起来是多少？")
        self.assertEqual(json.loads(await self.plugin.dsh_submit_file(event))["sources"], ["sample.csv"])
        with self.assertRaises(ValueError):
            await self.plugin.dsh_submit_file(event, "other.csv", "求和")
        with self.assertRaises(PermissionError):
            await self.plugin.dsh_submit_file(FakeEvent(user="99999"), "sample.csv", "求和")
        with patch.object(self.plugin, "start_commission_worker"):
            first = json.loads(await self.plugin.dsh_submit_file(event, "sample.csv", "求和", background=True))
            second = json.loads(await self.plugin.dsh_submit_file(event, "sample.csv", "求和", background=True))
        self.assertEqual((first["status"], first["billing"], first["id"]), ("running_in_background", "go", second["id"]))
        rows = json.loads(await self.plugin.dsh_history(FakeEvent(), kind="file", record_id=first["id"]))["tasks"]
        self.assertEqual([(row["id"], row["status"], row["source"]) for row in rows],
                         [(first["id"], "prepared", "sample.csv")])

    async def test_dsh_history_reads_the_recorded_file_through_original_session(self):
        cwd = r"C:\Runner\files"
        jobs = main.CommissionedFiles(self.plugin.store)
        ident = jobs.accept(SCOPE, "12345", "qq1", {"cwd": cwd, "source": "sample.csv", "instruction": "求和"})
        row = jobs.list(SCOPE, "12345")[0]
        self.plugin.store.setting(jobs._key(SCOPE, ident), {**row, "state": "completed", "session_id": "old-session"})
        file = row["report"]
        client = types.SimpleNamespace(call=AsyncMock(return_value={"text": "旧产物", "eof": True,
            "absolutePath": str(PureWindowsPath(cwd) / file)}), close=AsyncMock())
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=lambda _, source=None: cwd if source else client)
        result = json.loads(await self.plugin.dsh_history(FakeEvent(event_id="after-new"), record_id=ident, read_report=True))
        self.assertEqual(result["tasks"][0]["report_text"], "旧产物")
        client.call.assert_awaited_once_with("workspaceFiles.read", {"sessionId": "old-session", "path": file})
        self.plugin.context.fishhome_dsh_commission_client = lambda *_: r"C:\Different"
        result = json.loads(await self.plugin.dsh_history(FakeEvent(), record_id=ident, read_report=True))
        self.assertEqual(result["tasks"][0]["report_status"], "unavailable")
        self.assertEqual(client.call.await_count, 1)

    async def test_direct_dsh_tool_returns_a_quick_report_to_the_model_without_qq_delivery(self):
        from test_commissioned_files import FileRunner, PLAN

        jobs = main.CommissionedFiles(self.plugin.store)

        def factory(scope, source=None):
            if source:
                return PLAN["cwd"]
            pending = jobs.list(SCOPE, "12345", pending_only=True)
            if not pending:
                return types.SimpleNamespace(model_route="go")
            runner = FileRunner(pending[0])
            runner.model_route = "go"
            runner.go_receipts = AsyncMock(return_value={"receipts": [{"source": "go"}]})
            return runner

        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=factory)
        sender = types.SimpleNamespace(ready=lambda _: True, send=AsyncMock())
        self.plugin.sender = lambda: sender
        result = json.loads(await self.plugin.dsh_submit_file(FakeEvent(text="count 列有几行？"), "sample.csv", "统计数据行"))
        self.assertEqual((result["status"], result["result"]), ("completed", "sample.csv has two rows."))
        self.assertEqual(jobs.list(SCOPE, "12345")[0]["delivery"], "inline")
        sender.send.assert_not_awaited()

    async def test_owner_chat_gets_the_active_dsh_file_tool_and_default_guidance(self):
        tool = fixtures.FunctionTool(name=main.DSH_FILE_TOOL, active=True)
        full = types.SimpleNamespace(get_tool=lambda name: tool if name == main.DSH_FILE_TOOL else None)
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=lambda *_: ["sample.csv", "D:/private.txt"],
            get_llm_tool_manager=lambda: types.SimpleNamespace(get_full_tool_set=lambda: full))
        event = FakeEvent(text="sample.csv 的 count 加起来是多少？")
        local_names = ["astrbot_file_read_tool", "astrbot_execute_shell", "astrbot_execute_python", "astrbot_shell_session"]
        local = [fixtures.FunctionTool(name=name, active=True, description=name, parameters={}) for name in local_names]
        req = types.SimpleNamespace(func_tool=fixtures.ToolSet(local), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(event, req)
        self.assertIn(main.DSH_FILE_TOOL, req.func_tool.names())
        self.assertIn("astrbot_file_read_tool", req.func_tool.names())
        self.assertNotIn("astrbot_execute_shell", req.func_tool.names())
        self.assertNotIn("astrbot_execute_python", req.func_tool.names())
        self.assertNotIn("astrbot_shell_session", req.func_tool.names())
        self.assertIn("你自己决定用本地文件工具还是 df_dsh_submit_file", req.system_prompt)
        self.assertLess(req.system_prompt.index("【本轮 DSH 与文件能力】"), req.system_prompt.index("persona"))
        self.assertIn('["sample.csv"]', req.system_prompt)
        self.assertNotIn("D:/private.txt", req.system_prompt)
        outsider = types.SimpleNamespace(func_tool=fixtures.ToolSet([tool]), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(FakeEvent(scope=OTHER_SCOPE), outsider)
        self.assertNotIn(main.DSH_FILE_TOOL, outsider.func_tool.names())

    async def test_workspace_lookup_hints_only_after_a_local_miss(self):
        dsh = fixtures.FunctionTool(name=main.DSH_FILE_TOOL, active=True)
        read = fixtures.FunctionTool(name="astrbot_file_read_tool", active=True, description="read", parameters={},
            call=AsyncMock(side_effect=["Error reading file: [WinError 2] The system cannot find the file specified", "item,count\nlocal,6"]))
        grep = fixtures.FunctionTool(name="astrbot_grep_tool", active=True, description="grep", parameters={},
            call=AsyncMock(return_value="No matches found."))
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=lambda *_: ["sample.csv", "other.csv"])
        event = FakeEvent(text="count 列合计多少？")
        request = types.SimpleNamespace(func_tool=fixtures.ToolSet([dsh, read, grep]), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(event, request)
        tools = {tool.name: tool for tool in request.func_tool.tools}
        missed = await tools[read.name].call(None, path="sample.csv")
        self.assertIn('独立工作区已登记素材（存在性以执行为准）：["sample.csv"]', missed)
        self.assertIn(main.DSH_FILE_TOOL, missed)
        local = await tools[read.name].call(None, path="local.csv")
        self.assertEqual(local, "item,count\nlocal,6")
        searched = await tools[grep.name].call(None, pattern="count", glob="sample.csv")
        self.assertIn('"sample.csv"', searched)
        self.assertNotIn('"other.csv"', searched)
        self.assertEqual(main.CommissionedFiles(self.plugin.store).list(SCOPE, "12345"), [])

    async def test_named_file_question_is_left_for_chat_model_while_slash_commissions_go(self):
        def factory(_, source=None):
            if source == "":
                return ["sample.csv"]
            if source == "sample.csv":
                return r"C:\Runner\files"
            return types.SimpleNamespace(model_route="go")

        self.plugin.context = types.SimpleNamespace(fishhome_dsh_commission_client=factory)
        text = "sample.csv 的 count 列合计是多少？注明来源。"
        event = FakeEvent(text=text)
        event.get_platform_name = lambda: "aiocqhttp"
        event.message_obj.raw_message = {"post_type": "message", "message_type": "private", "sub_type": "friend"}
        await self.plugin.incoming(event)
        self.assertFalse(event.is_stopped())
        jobs = main.CommissionedFiles(self.plugin.store)
        self.assertEqual(jobs.list(SCOPE, "12345"), [])
        ack = self.plugin.owner_commission_command(event, "/委托 sample.csv " + text)
        self.assertIn("OpenCode Go", ack)
        row = jobs.list(SCOPE, "12345", explicit_only=True)[0]
        self.assertEqual((row["source"], row["instruction"]), ("sample.csv", text))

    async def test_voice_tool_can_be_chosen_without_a_keyword_and_suppresses_duplicate_text(self):
        event = FakeEvent(text="今天好累。")
        event.get_platform_name = lambda: "aiocqhttp"
        event.set_extra = event._extra.__setitem__
        event.clear_result = lambda: event.set_extra("cleared", True)
        event.message_obj.raw_message = {"post_type": "message", "message_type": "private", "sub_type": "friend"}
        provider = types.SimpleNamespace(provider_config={"id": "voice", "type": "dashscope_tts"},
            get_model=lambda: "qwen-audio-model", get_audio=AsyncMock(return_value="speech.wav"))
        tool = fixtures.FunctionTool(name=main.TTS_SPEAK_TOOL, active=True)
        registry = types.SimpleNamespace(get_tool=lambda name: tool if name == main.TTS_SPEAK_TOOL else None)
        self.plugin.context = types.SimpleNamespace(get_config=lambda **kwargs: {}, get_all_tts_providers=lambda: [provider],
            get_llm_tool_manager=lambda: types.SimpleNamespace(get_full_tool_set=lambda: registry))
        sender = types.SimpleNamespace(send=AsyncMock(return_value={"message_id": "42", "record_readback": True}))
        self.plugin.sender = lambda: sender
        layout = types.SimpleNamespace(make_tts_text=lambda text: text)
        req = types.SimpleNamespace(func_tool=fixtures.ToolSet(), system_prompt="persona", contexts=[])
        await self.plugin.guard_phone(event, req)
        self.assertIn(main.TTS_SPEAK_TOOL, req.func_tool.names())
        with patch.dict(sys.modules, {"astrbot.core.pipeline.chat_layout": layout}):
            result = json.loads(await self.plugin.tts_speak(event, "先歇一会儿。"))
            await self.plugin.tts_speak(event, "不再发一条。")
        self.assertTrue(result["sent"])
        sender.send.assert_awaited_once_with(SCOPE, "", record_path="speech.wav")
        await self.plugin.suppress_voice_only_confirmation(event)
        self.assertTrue(event.get_extra("cleared"))
        event.message_obj.message_str = "这轮只用文字回复"
        with self.assertRaises(PermissionError):
            await self.plugin.tts_speak(event, "照样发语音")

    async def test_personal_step_can_save_and_wait_for_low_cost(self):
        event = self.begin("personal-scheduling")
        goal = await self.create(event)
        now = main.time.time()
        with patch.object(main, "peak_end", return_value=now + 1800):
            small = json.loads(await self.plugin.goal(event, "schedule_step", goal_id=goal["id"], estimated_tokens=500))
            self.assertEqual(small["status"], "available")
            result = json.loads(await self.plugin.goal(event, "schedule_step", goal_id=goal["id"],
                                                       estimated_tokens=30000, next_step="继续测试"))
        self.assertEqual(result["status"], "deferred")
        saved = self.plugin.store.life.get_goal(SCOPE, goal["id"])
        self.assertEqual((saved["status"], saved["next_step"]), ("paused", "继续测试"))
        self.assertEqual(saved["checkpoint"]["personal_cost_wait"]["resume_after"], now + 1800)

    def save_config(self):
        self.plugin.control.write_text(json.dumps(self.config))

    def begin(self, run_id="run"):
        self.plugin.activity_budget().start_run(SCOPE, run_id, 600, self.config)
        self.plugin.life_runs.add(run_id)
        self.plugin.run_videos[run_id] = set()
        return FakeEvent(run_id=run_id)

    async def create(self, event):
        return json.loads(await self.plugin.goal(event, "create", title="研究建造玩法",
                                                why="想弄清合作机制", next_step="看实机演示"))

    async def watched(self, event):
        await self.plugin.watch(event, "BVexample", "1", 0, 20)
        return json.loads(await self.plugin.activity_note(event, "BVexample", "1", 20, "想再查合作机制"))

    async def test_video_delegation_uses_only_link_and_question_and_writes_separate_report(self):
        target = "https://www.bilibili.com/video/BV1g8Wq6DEXj?p=1"
        report = {"kind": "dsh_video", "task_id": "delegated-1", "summary": "代看了开头",
                  "evidence": [{"source": target, "frame_times": [0, 7.5], "subtitle_count": 0}],
                  "billing": [{"source": "go"}]}
        worker = AsyncMock(return_value=report)
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda scope: worker if scope == SCOPE else None)
        event = self.begin()
        goal = await self.create(event)
        result = json.loads(await self.plugin.delegate_video(event, target, "开头在做什么"))
        worker.assert_awaited_once_with(target, "开头在做什么")
        self.assertEqual(self.calls, [])
        saved = self.plugin.store.life.episodes(SCOPE, kind="dsh_video")
        self.assertEqual([(item["id"], item["subject_kind"]) for item in saved], [(result["episode_id"], "system")])
        self.assertEqual(self.plugin.store.life.episodes(SCOPE, kind="video"), [])
        self.assertEqual(saved[0]["evidence"]["samples"], report["evidence"])
        self.assertEqual(result["summary"], "代看了开头")
        completed = await self.plugin.goal(event, "update", goal_id=goal["id"], status="completed")
        self.assertEqual(json.loads(completed)["status"], "completed")
        await self.plugin.watch(FakeEvent(), "BV1g8Wq6DEXj", "1", 0, 20, mode="personal")
        self.assertEqual(self.plugin.store.life.episodes(SCOPE, kind="video")[0]["subject_kind"], "agent")

    async def test_video_delegation_failure_does_not_watch_or_write_a_report(self):
        worker = AsyncMock(side_effect=ValueError("没有订阅回执"))
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda scope: worker)
        event = FakeEvent()
        result = json.loads(await self.plugin.delegate_video(event, "BV1g8Wq6DEXj", "核对开头"))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plugin.store.life.episodes(SCOPE, kind="dsh_video"), [])

    async def test_existing_video_tool_defaults_to_dsh_locks_part_and_allows_explicit_personal(self):
        worker = AsyncMock(return_value={"kind": "dsh_video", "task_id": "one", "summary": "代看",
                                         "evidence": [{"cid": 11, "frame_times": [0]}], "billing": []})
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda scope: worker)
        event = FakeEvent()
        result = json.loads(await self.plugin.watch(event, "BV1g8Wq6DEXj", "11", 0, 8))
        self.assertEqual(result["kind"], "dsh_video")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls, [])
        result = json.loads(await self.plugin.watch(event, "BV1g8Wq6DEXj", "12", 0, 8))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(self.calls, [])
        await self.plugin.watch(event, "BV1g8Wq6DEXj", "11", 0, 8, mode="personal")
        self.assertEqual(self.calls[0][0], "bili_prepare_video")

    async def test_go_defaults_to_personal_video_and_keeps_delegated_search(self):
        delegate = AsyncMock()
        provider = types.SimpleNamespace(provider_config={"type": "openai_chat_completion",
            "model": "deepseek-v4.1-flash", "api_base": "https://opencode.ai/zen/go/v1"})
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda _: delegate,
            fishhome_dsh_web_search_client=lambda _: AsyncMock(),
            get_using_provider_async=AsyncMock(return_value=provider))
        event = FakeEvent()
        await self.plugin.watch(event, "BV1g8Wq6DEXj", "11", 0, 8)
        self.assertEqual(self.calls[0][0], "bili_prepare_video")
        delegate.assert_not_awaited()
        self.assertIn("优先用 df_bili_watch", self.plugin.viewing_guidance(self.config, True))
        self.assertIn("查公开网页时优先用 df_web_search",
                      self.plugin.capability_context(self.config, go_video=True))

    async def test_personal_video_continues_one_batch_at_a_time(self):
        async def prepare(_tool, args):
            return {"identity": {"bvid": args["bvid"], "cid": args["cid"], "duration": 810},
                    "range": [args["start"], min(args["end"], 810)]}

        self.backend.json_call.side_effect = prepare
        event = FakeEvent()
        bvid = "BV1g8Wq6DEXj"
        start, end = 100, 900
        for expected in ([400, 810], [700, 810], None):
            result = json.loads(await self.plugin.watch(event, bvid, "11", start, end, mode="personal"))
            self.assertEqual(result.get("next_range"), expected)
            self.assertEqual(result["range"][0], start)
            if expected:
                start, end = expected
        self.assertEqual([(call.args[1]["start"], call.args[1]["end"]) for call in
                          self.backend.json_call.await_args_list], [(100, 400), (400, 700), (700, 810)])

    async def test_default_delegation_returns_the_remaining_range_for_the_same_part(self):
        worker = AsyncMock(return_value={"kind": "dsh_video", "task_id": "first", "summary": "代看首批",
                                         "evidence": [{"cid": 11, "duration": 810, "frame_times": [100]}],
                                         "billing": []})
        self.plugin.context = types.SimpleNamespace(fishhome_dsh_video_client=lambda _: worker)
        event = FakeEvent()
        result = json.loads(await self.plugin.watch(event, "BV1g8Wq6DEXj", "11", 100, 900))
        self.assertEqual(result["next_range"], [400, 810])
        self.assertIn("cid 11", worker.await_args.args[1])
        self.assertIn("100 到 400 秒", worker.await_args.args[1])
        self.assertEqual(self.calls, [])
        worker.return_value["evidence"][0]["cid"] = 12
        result = json.loads(await self.plugin.watch(event, "BV1g8Wq6DEXj", "11", 100, 900))
        self.assertEqual(result["status"], "needs_review")
        self.assertNotIn("next_range", result)

    async def test_dsh_goal_recovers_accepted_request_and_writes_back_once(self):
        store = self.plugin.store
        goal = store.life.create_goal(SCOPE, "文件研究", "保留出处", "汇总样例", "file-goal")
        goal_id = goal["id"]
        store.life.update_goal(SCOPE, goal_id, checkpoint={"original_progress": 20})
        key = dsh_task.task_key(SCOPE, goal_id)
        cwd = r"C:\FishHome\test-workspace"
        self.config.update(life_decision_enabled=True, dnd_ranges=[], dsh_life_task={
            "goal_id": goal_id, "cwd": cwd, "source": "sample.csv", "instruction": "汇总样例"})
        self.save_config()
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        self.plugin.agent = AsyncMock(side_effect=AssertionError("the phone agent must not run"))
        network = "interrupt"
        submitted = []

        async def rpc(method, args):
            if method == "session.create":
                return {"sessionId": "session-local"}
            if method == "session.selectModel":
                return {"selected": {"provider": "deepseek-official", "model": "deepseek-flash"}}
            if method == "session.prompt":
                task = self.plugin.store.get_setting(key)
                self.assertEqual(task["state"], "submitting")
                self.assertEqual(task["request_id"], args["requestId"])
                submitted.append(args["requestId"])
                raise asyncio.CancelledError
            if network == "offline":
                raise ConnectionError("local mock disconnected")
            if method == "session.state":
                return {"running": False, "queue": [], "pendingInput": []}
            if method == "session.history":
                return {"events": [
                    {"type": "user/message", "data": {"source": {"kind": "user", "rpcId": submitted[0]}}},
                    {"type": "user/message", "data": {"source": {"kind": "runtime-context"}}},
                    {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
                ]}
            if method == "workspaceFiles.read":
                path = args["path"]
                text = '{"source":"sample.csv"}' if path.endswith(".json") else "引用 sample.csv"
                return {"absolutePath": str(PureWindowsPath(cwd) / path), "eof": True, "text": text}
            if method == "session.projections":
                return {"values": {"tokenUsage": {"outputTokens": 8}}}
            raise AssertionError(method)

        client = types.SimpleNamespace(call=AsyncMock(side_effect=rpc), close=AsyncMock())
        self.plugin.context = types.SimpleNamespace(**{dsh_task.BRIDGE: lambda scope: client})
        before = datetime.fromisoformat("2026-11-08T23:59:50+08:00").timestamp()
        with patch.object(main.time, "time", return_value=before - 1):
            await self.plugin.run_activity("periodic")
            client.call.assert_not_awaited()
        with patch.object(main.time, "time", return_value=before), patch.object(dsh_task, "time", return_value=before):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.run_activity("once")
        first = store.get_setting(key)
        self.assertEqual(first["origin_day"], "2026-11-08")
        self.assertEqual(first["billing"], "autonomous")
        store.close()
        self.plugin.store = store = core.Store(self.plugin.folder)
        self.plugin.budget = main.Budget(store)

        # Give the original planner its normal retry cooldown between attempts.
        retry_interval = self.plugin.cfg()["life_repeat_cooldown_minutes"] * 60 + 1
        with patch.object(main.time, "time", return_value=before + retry_interval) as clock:
            network = "offline"
            await self.plugin.run_activity("once")
            self.assertEqual(store.get_setting(key)["state"], "needs_review")
            network = "online"
            clock.return_value += retry_interval
            with patch.object(store, "queue_memory", side_effect=RuntimeError("interrupted writeback")):
                await self.plugin.run_activity("once")
            self.assertEqual(store.get_setting(key)["state"], "needs_review")
            with self.assertRaisesRegex(ValueError, "needs observed content"):
                store.life.update_goal(SCOPE, goal_id, status="completed")
            clock.return_value += retry_interval
            await self.plugin.run_activity("once")

        task = store.get_setting(key)
        self.assertEqual(task["state"], "completed")
        self.assertEqual((task["task_id"], task["origin_day"], task["billing"]),
                         (first["task_id"], first["origin_day"], first["billing"]))
        self.assertEqual(submitted, [first["request_id"]])
        self.assertEqual(store.life.get_goal(SCOPE, goal_id)["checkpoint"]["original_progress"], 20)
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM episodes WHERE kind='dsh_file'").fetchone()[0], 1)
        attempts = store.life.episodes(SCOPE, kind="dsh_file_attempt")
        reports = store.life.episodes(SCOPE, kind="dsh_file")
        self.assertEqual([row["evidence"]["status"] for row in attempts], ["needs_review"])
        self.assertEqual(reports[0]["evidence"]["report_excerpt"], "引用 sample.csv")
        note = json.loads(await self.plugin.activity_note(FakeEvent(), episode_id=reports[0]["id"], thought="样例有点小"))
        self.assertEqual(note["source_episode_id"], reports[0]["id"])
        self.assertEqual(store.life.episodes(SCOPE, kind="dsh_file_note")[0]["interpretation"], {"thought": "样例有点小"})
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM memories WHERE id LIKE 'dsh-file:%'").fetchone()[0], 1)
        checkpoint = store.life.get_goal(SCOPE, goal_id)["checkpoint"]
        store.life.update_goal(SCOPE, goal_id, checkpoint={**checkpoint, "dsh_file": {
            **checkpoint["dsh_file"], "report": "output/other.md"}})
        with self.assertRaisesRegex(ValueError, "needs observed content"):
            store.life.update_goal(SCOPE, goal_id, status="completed")
        store.life.update_goal(SCOPE, goal_id, checkpoint=checkpoint)
        completed = store.life.update_goal(SCOPE, goal_id, status="completed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.calls, [])
        self.plugin.agent.assert_not_awaited()

    async def test_agent_queues_scoped_file_task_and_periodic_run_uses_file_preset(self):
        goal = self.plugin.store.life.create_goal(SCOPE, "文件研究", "核对出处", "汇总资料", "agent-file")
        self.config.update(dsh_agent_tasks_enabled=True, life_decision_enabled=True, dnd_ranges=[])
        self.save_config()
        cwd = r"C:\FishHome\approved"
        client = types.SimpleNamespace(call=AsyncMock(return_value={"sessionId": "bad-preset"}), close=AsyncMock())

        def bridge(scope, source=None, *, standalone=False):
            self.assertEqual(scope, SCOPE)
            if standalone:
                return cwd
            if source is None:
                return client
            if source == "":
                return ["sample.csv"]
            if source != "sample.csv":
                raise PermissionError("not approved")
            return cwd

        self.plugin.context = types.SimpleNamespace(**{dsh_task.BRIDGE: bridge})
        with self.assertRaises(PermissionError):
            await self.plugin.file_research(FakeEvent(), "list")
        event = self.begin("plan")
        self.plugin.store.life.claim_goal(SCOPE, goal["id"], "plan")
        self.plugin.run_goals["plan"] = goal["id"]
        with self.assertRaises(PermissionError):
            await self.plugin.file_research(event, "queue", "../private.csv", "核对来源")
        self.assertEqual(json.loads(await self.plugin.file_research(event, "queue", "sample.csv", "核对来源"))["status"], "queued")
        self.assertEqual(json.loads(await self.plugin.file_research(event, "queue", "sample.csv", "另一项"))["status"], "already_queued")
        client.call.assert_not_called()
        self.plugin.store.life.pause_run(SCOPE, "plan", "done")
        self.plugin.activity_budget().finish_run(SCOPE, "plan")
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        low = datetime.fromisoformat("2026-11-13T22:00:00+08:00").timestamp()
        with patch.object(dsh_task, "time", return_value=low):
            await self.plugin.run_activity("periodic")
        client.call.assert_awaited_once_with("session.create", {"cwd": cwd, "agentPreset": "fishhome-files"})

    async def test_agent_can_create_a_draft_without_source_files(self):
        self.config["dsh_agent_tasks_enabled"] = True
        self.save_config()
        self.plugin.context = types.SimpleNamespace(**{dsh_task.BRIDGE: lambda scope, *, standalone: r"C:\FishHome\approved"})
        event = self.begin("draft")
        draft = self.plugin.store.life.create_goal(SCOPE, "写一份计划", "自主创作", "起草三步", "draft-file")
        self.plugin.store.life.claim_goal(SCOPE, draft["id"], "draft")
        self.plugin.run_goals["draft"] = draft["id"]
        with self.assertRaises(ValueError):
            await self.plugin.file_research(event, "create", "sample.csv", "起草观察计划")
        result = json.loads(await self.plugin.file_research(event, "create", instruction="起草观察计划"))
        self.assertEqual((result["status"], result["source"]), ("queued", None))

    async def test_queued_go_file_run_does_not_require_or_consume_personal_activity_budget(self):
        store = self.plugin.store
        goal = store.life.create_goal(SCOPE, "文件研究", "保留出处", "汇总样例", "go-budget-goal")
        store.life.update_goal(SCOPE, goal["id"], checkpoint={"personal_cost_wait": {"resume_after": datetime.now().timestamp() + 3600}})
        plan = {"goal_id": goal["id"], "cwd": r"C:\FishHome\approved", "source": "sample.csv",
                "instruction": "汇总样例"}
        self.config.update(life_decision_enabled=True, dnd_ranges=[], dsh_life_task=plan)
        self.save_config()
        client = types.SimpleNamespace(model_route="go", close=AsyncMock())
        self.plugin.context = types.SimpleNamespace(**{dsh_task.BRIDGE: lambda scope: client})
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        budget = self.plugin.activity_budget()
        with patch.object(budget, "snapshot", return_value={"remaining_seconds": 0, "remaining_model_calls": 0}), \
                patch.object(budget, "start_run") as charge, patch.object(budget, "finish_run") as settle, \
                patch.object(main, "run_file_task", new_callable=AsyncMock) as execute:
            execute.return_value = ("completed", "Done")
            self.assertTrue(self.plugin.life_ready(self.plugin.cfg()))
            await self.plugin.run_activity("once")
        execute.assert_awaited_once()
        self.assertEqual(execute.await_args.args[3], goal["id"])
        charge.assert_not_called()
        settle.assert_not_called()

    async def test_dsh_peak_defers_expensive_goal_and_reoffers_after_noon(self):
        store = self.plugin.store
        goal = store.life.create_goal(SCOPE, "文件研究", "保留出处", "汇总样例", "peak-goal")
        plan = {"goal_id": goal["id"], "cwd": r"C:\FishHome\test-workspace", "source": "sample.csv",
                "instruction": "汇总样例", "estimated_tokens": 32000}
        self.config.update(life_decision_enabled=True, dnd_ranges=[], dsh_life_task=plan)
        self.save_config()
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        client = types.SimpleNamespace(call=AsyncMock(side_effect=ConnectionError()), close=AsyncMock())
        self.plugin.context = types.SimpleNamespace(**{dsh_task.BRIDGE: lambda scope: client})
        before = datetime.fromisoformat("2026-11-09T11:59:00+08:00").timestamp()
        after = datetime.fromisoformat("2026-11-09T12:00:00+08:00").timestamp()
        key = dsh_task.task_key(SCOPE, goal["id"])
        with patch.object(main.time, "time", return_value=before), patch.object(dsh_task, "time", return_value=before):
            await self.plugin.run_activity("once")
        deferred = store.get_setting(key)
        self.assertEqual((deferred["state"], deferred["resume_after"]), ("deferred", after))
        client.call.assert_not_awaited()
        alternative = store.life.create_goal(SCOPE, "另一项研究", "继续资料整理", "核对另一处来源", "other-goal")
        choice = self.plugin.life_planner().choose(SCOPE, self.plugin.cfg(), now=before)
        self.assertEqual(choice.get("goal_id"), alternative["id"])
        self.assertIn({"key": "goal:" + goal["id"], "reason": "dsh_deferred_until_low_cost"}, choice["excluded"])
        store.life.update_goal(SCOPE, alternative["id"], status="cancelled")
        with patch.object(main.time, "time", return_value=before), patch.object(dsh_task, "time", return_value=before):
            await self.plugin.run_activity("periodic")
        client.call.assert_not_awaited()
        with patch.object(main.time, "time", return_value=after), patch.object(dsh_task, "time", return_value=after):
            await self.plugin.run_activity("periodic")
        client.call.assert_awaited_once_with("session.create", {"cwd": plan["cwd"]})
        self.assertEqual(store.get_setting(key)["task_id"], deferred["task_id"])

    async def test_two_runs_keep_goal_checkpoint_interest_and_approval_once(self):
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        captured = {}

        async def agent(event, prompt, tools, max_steps):
            self.assertIn("bilibili_not_focused", prompt)
            self.assertIn('"identity_verified":false', prompt)
            self.assertTrue(main.LIFE_TOOLS <= tools)
            captured["goal"] = await self.create(event)
            note = await self.watched(event)
            await self.plugin.interest(event, note["episode_id"], "合作建造", polarity=1,
                                       thought="这段配合挺有意思")
            proposal = json.loads(await self.plugin.propose(event, "follow", '{"uid":99}', "想继续看演示"))
            captured["proposal_id"] = proposal["proposal_id"]
            return "已看演示，等待主人答复是否关注。"

        self.plugin.agent = agent
        await self.plugin.run_activity("once")
        goal_id = captured["goal"]["id"]
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal_id)["status"], "waiting_approval")
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM runs").fetchone()[0], "completed")
        self.assertTrue(self.plugin.store.db.execute("SELECT 1 FROM memories WHERE id LIKE 'episode:%'").fetchone())
        await self.plugin.incoming(FakeEvent(event_id="approved", text="可以"))
        await self.plugin.incoming(FakeEvent(event_id="approved", text="可以"))
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 1)
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal_id)["status"], "paused")
        self.assertEqual(self.plugin.store.life.interests(SCOPE, "user"), [])
        self.plugin.store.close()
        self.plugin.store = core.Store(self.plugin.folder)
        self.plugin.budget = main.Budget(self.plugin.store)
        event = self.begin("second-run")
        resumed = json.loads(await self.plugin.goal(event, "select", goal_id=goal_id, why="继续查昨天的问题"))
        self.assertEqual(resumed["checkpoint"]["position"], 20)
        self.assertEqual(resumed["next_step"], "看实机演示")
        self.assertEqual(len(self.plugin.store.life.interests(SCOPE)), 1)
        self.assertIn("合作建造", await self.plugin.recall(event, "合作建造"))
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 1)

    async def test_known_disabled_write_frees_slot_and_says_not_executed(self):
        event = FakeEvent(event_id="proposal")
        await self.plugin.propose(event, "follow", '{"uid":99}', "想关注")
        self.config["collection_management"] = False
        self.save_config()
        answer = FakeEvent(event_id="answer", text="可以")
        await self.plugin.incoming(answer)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM proposals").fetchone()[0], "blocked")
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 0)
        self.assertIn("没有执行", answer.send.await_args.args[0])
        self.config["collection_management"] = True
        self.save_config()
        await self.plugin.propose(FakeEvent(event_id="new-proposal"), "follow", '{"uid":100}', "新提议")
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM proposals").fetchone()[0], 2)

    async def test_preemption_failure_does_not_claim_approval(self):
        await self.plugin.propose(FakeEvent(), "follow", '{"uid":99}', "想关注")
        self.plugin.pause_background = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            await self.plugin.incoming(FakeEvent(event_id="answer", text="可以"))
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM proposals").fetchone()[0], "offered")
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 0)

    async def test_operation_switch_is_reread_after_awaiting_preemption(self):
        await self.plugin.propose(FakeEvent(), "follow", '{"uid":99}', "想关注")

        async def switch_off(reason):
            self.config["collection_management"] = False
            self.save_config()

        self.plugin.pause_background = switch_off
        await self.plugin.incoming(FakeEvent(event_id="answer", text="可以"))
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 0)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM proposals").fetchone()[0], "blocked")

    async def test_expired_offer_releases_goal_without_executing(self):
        event = self.begin()
        goal = await self.create(event)
        await self.plugin.propose(event, "follow", '{"uid":99}', "想关注")
        self.plugin.store.db.execute("UPDATE proposals SET expires=0")
        await self.plugin.goal(event, "list")
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal["id"])["status"], "paused")
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 0)

    async def test_foreground_explicit_preference_and_scope_are_separate(self):
        await self.plugin.incoming(FakeEvent(text="我喜欢合作建造", event_id="preference"))
        await self.plugin.incoming(FakeEvent(text="我喜欢合作建造", event_id="preference"))
        self.assertEqual(len(self.plugin.store.life.interests(SCOPE, "user")), 1)
        self.assertEqual(self.plugin.store.life.interests(SCOPE), [])
        await self.plugin.incoming(FakeEvent(scope=OTHER_SCOPE, text="我喜欢别的话题", event_id="other"))
        self.assertEqual(self.plugin.store.life.interests(OTHER_SCOPE, "user"), [])
        with self.assertRaises(PermissionError):
            await self.plugin.recall(FakeEvent(scope=OTHER_SCOPE))

    async def test_unselected_goal_blocks_reads_before_network(self):
        event = self.begin()
        with self.assertRaises(ValueError):
            await self.plugin.read(event, "home")
        self.assertEqual(self.calls, [])

    async def test_failed_read_is_returned_and_audited_without_read_episode(self):
        event = self.begin()
        goal = await self.create(event)
        failure = {"ok": False, "error": "TimeoutError", "verified": False}
        self.backend.json_call.side_effect = None
        self.backend.json_call.return_value = failure

        self.assertEqual(json.loads(await self.plugin.read(event, "home")), failure)
        self.assertEqual(self.plugin.store.db.execute(
            "SELECT count(*) FROM episodes WHERE goal_id=? AND kind='read'", (goal["id"],)).fetchone()[0], 0)
        audit = self.plugin.store.db.execute(
            "SELECT details FROM events WHERE scope=? AND kind='read_failed'", (SCOPE,)).fetchone()
        self.assertEqual(json.loads(audit[0]), {"error": "TimeoutError", "operation": "home"})

    async def test_read_accepts_decoded_and_string_json_arguments(self):
        event = self.begin()
        await self.create(event)
        self.backend.json_call.side_effect = None
        self.backend.json_call.return_value = {"identity": {"bvid": "BVexample", "cid": 1}, "items": []}

        result = json.loads(await self.plugin.read(event, "home", {}))
        self.assertEqual(result["items"], [])
        self.assertEqual(self.backend.json_call.await_args.args[1]["args"], {})

        await self.plugin.read(event, "home", "{}")
        self.assertEqual(self.backend.json_call.await_args.args[1]["args"], {})

        with self.assertRaisesRegex(ValueError, "JSON object"):
            await self.plugin.read(event, "home", "[]")

    async def test_empty_observation_cannot_create_interest(self):
        event = self.begin()
        await self.create(event)
        episode = self.plugin.record_video(event, {"bvid": "BVempty", "cid": 1, "observations": [], "subtitles": []})
        with self.assertRaises(ValueError):
            await self.plugin.interest(event, episode["id"], "游戏")

    async def test_media_calls_share_budget_and_keep_partial_evidence(self):
        self.config["max_model_calls_per_run"] = 1
        self.save_config()
        event = self.begin()
        await self.create(event)
        self.backend.json_call.side_effect = None
        self.backend.json_call.return_value = {
            "identity": {"bvid": "BVexample", "cid": 1}, "range": [0, 20],
            "subtitles": [{"from": 0, "to": 20, "content": "已读取字幕"}],
            "frames": [{"at": i, "data": base64.b64encode(b"image").decode()} for i in range(5)]}
        face = types.SimpleNamespace(_vl_describe=AsyncMock(return_value="第一批真实画面"), _asr_file=AsyncMock())
        self.plugin.face_plugin = lambda: face
        with self.assertRaises(main.BudgetExceeded):
            await self.plugin.watch(event, "BVexample", "1", 0, 20)
        self.assertEqual(face._vl_describe.await_count, 1)
        self.assertEqual(len(self.plugin.store.history(SCOPE)[0]["observations"]), 1)
        self.assertIn("第一批真实画面", await self.plugin.recall(event, "第一批"))

    async def test_zero_share_budget_blocks_send_but_not_passive_screenshot(self):
        self.config["daily_shares"] = 0
        self.save_config()
        event = self.begin()
        await self.create(event)
        with self.assertRaises(main.BudgetExceeded):
            await self.plugin.share_thought(event, "这个机制挺好玩")
        self.delivery.send.assert_not_called()
        fixtures.PluginTests.snapshot(self, owner=None)
        await self.plugin.share(FakeEvent(event_id="passive"), "shot", "你要的截图", passive=True)
        self.assertEqual(self.delivery.send.await_count, 1)

    async def test_pause_cancels_run_preserves_goal_and_resumes_with_cooldown(self):
        self.config["chat_cooldown_seconds"] = 300
        self.save_config()
        event = self.begin()
        goal = await self.create(event)
        entered = asyncio.Event()

        async def background():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                self.plugin.store.life.pause_run(SCOPE, "run", "cancelled")

        self.plugin.live = asyncio.create_task(background())
        await entered.wait()
        await self.plugin.incoming(FakeEvent(text="暂停自主活动"))
        self.assertTrue(self.plugin.live.done())
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal["id"])["status"], "paused")
        self.assertFalse(self.plugin.life_ready(self.config))
        await self.plugin.incoming(FakeEvent(text="继续自主活动", event_id="resume"))
        self.assertFalse(self.plugin.store.get_setting("life_paused:" + SCOPE))
        self.assertFalse(self.plugin.life_ready(self.config))

    async def test_silent_run_does_not_invent_goal_or_send_message(self):
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        self.plugin.agent = AsyncMock(return_value="暂无想做的事，安静结束。")
        await self.plugin.run_activity("once")
        self.assertEqual(self.plugin.store.life.goals(SCOPE), [])
        self.delivery.send.assert_not_called()
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM runs").fetchone()[0], "completed")
        self.assertFalse(self.plugin.life_runs)

    async def test_feature_switch_off_stops_existing_life_calls(self):
        event = self.begin()
        await self.create(event)
        self.config["life_enabled"] = False
        self.save_config()
        with self.assertRaises(PermissionError):
            await self.plugin.read(event, "home")
        with self.assertRaises(PermissionError):
            self.plugin.charge_model(event)
        self.assertEqual(self.calls, [])

    async def test_goal_next_step_does_not_change_running_status(self):
        event = self.begin()
        goal = await self.create(event)
        updated = json.loads(await self.plugin.goal(
            event, "update", goal_id=goal["id"], next_step="继续核对合作人数"))
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["next_step"], "继续核对合作人数")

    async def test_foreground_can_complete_goal_paused_by_preemption(self):
        event = self.begin()
        goal = await self.create(event)
        self.plugin.store.life.record_episode(SCOPE, kind="video", source_id="foreground-proof",
            goal_id=goal["id"], run_id="run", evidence={
                "bvid": "BVexample", "cid": 1,
                "subtitles": [{"from": 0, "to": 5, "content": "真实读取"}]})

        async def pause(reason):
            self.plugin.store.life.pause_run(SCOPE, "run", reason)

        self.plugin.pause_background = AsyncMock(side_effect=pause)
        completed = json.loads(await self.plugin.goal(
            FakeEvent(), "update", goal_id=goal["id"], status="completed"))
        self.assertEqual(completed["status"], "completed")
        self.plugin.pause_background.assert_awaited_once()

    async def test_other_session_phone_wrapper_is_rejected_before_gate(self):
        original = types.SimpleNamespace(name="list_devices", description="devices", active=True,
                                         parameters={"type": "object", "properties": {}},
                                         mcp_tool=types.SimpleNamespace(name="list_devices"))
        self.plugin.pause_background = AsyncMock()
        self.plugin.gate = types.SimpleNamespace(call=AsyncMock(return_value="ok"))
        self.plugin.phone = lambda: "phone"
        tool = main.GuardedPhoneTool(original, self.plugin)
        context = types.SimpleNamespace(context=types.SimpleNamespace(event=FakeEvent(scope=OTHER_SCOPE)))
        with self.assertRaises(PermissionError):
            await tool.call(context)
        self.plugin.pause_background.assert_not_called()
        self.plugin.gate.call.assert_not_called()
        context.context.event = FakeEvent()
        await tool.call(context)
        self.plugin.pause_background.assert_awaited_once()
        self.plugin.gate.call.assert_awaited_once()

    async def test_nonowner_phone_and_raw_bili_tools_are_removed(self):
        def mcp_tool(name, server):
            return types.SimpleNamespace(
                name=name, description=name, active=True,
                parameters={"type": "object", "properties": {}},
                mcp_tool=types.SimpleNamespace(name=name), mcp_server_name=server)

        request = types.SimpleNamespace(system_prompt="", func_tool=fixtures.ToolSet([
            mcp_tool("list_devices", "PhoneMCP"),
            mcp_tool("listen_audio", "phone-mcp"),
            mcp_tool("wait", "unlabelled"),
            mcp_tool("bili_read", "unlabelled"),
            mcp_tool("future_internal_tool", "dafeiyu-bili-activity"),
        ]))
        await self.plugin.guard_phone(FakeEvent(scope=OTHER_SCOPE), request)
        self.assertEqual(request.func_tool.names(), [])

        owner_request = types.SimpleNamespace(system_prompt="", func_tool=fixtures.ToolSet([
            mcp_tool("list_devices", "PhoneMCP"),
            mcp_tool("listen_audio", "phone-mcp"),
            mcp_tool("wait", "unlabelled"),
            mcp_tool("bili_read", "unlabelled"),
            mcp_tool("future_internal_tool", "dafeiyu-bili-activity"),
        ]))
        await self.plugin.guard_phone(FakeEvent(), owner_request)
        self.assertEqual(owner_request.func_tool.names(), ["list_devices", "listen_audio", "wait"])
        self.assertTrue(all(isinstance(tool, main.GuardedPhoneTool)
                            for tool in owner_request.func_tool.tools))

    async def test_unrelated_server_names_and_same_named_tools_are_untouched_for_owner(self):
        def mcp_tool(name, server):
            return types.SimpleNamespace(
                name=name, description=name, active=True,
                parameters={"type": "object", "properties": {}},
                mcp_tool=types.SimpleNamespace(name=name), mcp_server_name=server)

        original = [
            mcp_tool("listen_audio", "microphone-tools"),
            mcp_tool("wait", "stability-ai"),
        ]
        request = types.SimpleNamespace(system_prompt="", func_tool=fixtures.ToolSet(original))
        await self.plugin.guard_phone(FakeEvent(), request)
        self.assertEqual(request.func_tool.tools, original)
        self.assertFalse(any(isinstance(tool, main.GuardedPhoneTool)
                             for tool in request.func_tool.tools))


if __name__ == "__main__":
    unittest.main(verbosity=2)
