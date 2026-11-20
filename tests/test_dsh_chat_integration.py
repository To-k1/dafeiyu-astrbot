"""Public chat tool enqueues immediately; background results use the existing QQ outbox."""
import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from test_bili_activity import main, core, FakeEvent, SCOPE
from bili_activity_test.dsh_chats import DshChats
from bili_activity_test.dsh_meetings import DshMeetings
from bili_activity_test.dsh_roster import DshRoster


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.plugin = main.BiliActivity.__new__(main.BiliActivity)
        self.plugin.store = core.Store(self.folder.name)
        self.plugin.control = Path(self.folder.name, 'control.json')
        self.config = main.DEFAULTS | {'owner_scope': SCOPE, 'owner_id': '12345', 'enabled': True,
                                      'dnd_ranges': []}
        self.plugin.control.write_text(json.dumps(self.config))
        self.plugin.life_runs = set()
        self.plugin.context = types.SimpleNamespace()
        self.plugin.start_dsh_chat_worker = Mock()
        self.plugin.autonomous_team_allowed = lambda *_: True
        self.jobs = DshChats(self.plugin.store, SCOPE)

    async def asyncTearDown(self):
        self.plugin.store.close()
        self.folder.cleanup()

    async def test_public_send_never_waits_for_model_and_queries_do_not_send(self):
        agent = DshRoster(self.plugin.store, SCOPE).create('Fixture', 'Brief persona.')
        event = FakeEvent()
        first = json.loads(await asyncio.wait_for(self.plugin.dsh_chat(event, 'First question', agent['id']), .5))
        second = json.loads(await self.plugin.dsh_chat(event, 'Second question', agent['id']))
        self.assertEqual(first['status'], 'queued')
        self.assertTrue(first['async'])
        self.assertNotEqual(first['id'], second['id'])
        original = json.loads(await self.plugin.dsh_chat(event, action='get', chat_id=first['id']))
        self.assertEqual(original['message'], 'First question')
        self.assertEqual(len(self.jobs.list()), 2)
        self.assertEqual(self.plugin.start_dsh_chat_worker.call_count, 2)

    async def test_named_session_management_keeps_identity_and_queued_destination(self):
        roster = DshRoster(self.plugin.store, SCOPE)
        agent = roster.create('Fixture', 'Original persona.')
        event = FakeEvent()
        self.plugin.dsh_chat_lock = lambda *_: asyncio.Lock()
        async def manage(action, **fields):
            return json.loads(await self.plugin.dsh_chat(event, agent_id=agent['id'], action=action, **fields))
        first = (await manage('session_new', title='Agreement'))['session']
        queued = json.loads(await self.plugin.dsh_chat(event, 'Remember this agreement', agent['id']))
        second = (await manage('session_new', title='Research'))['session']
        listed = await manage('session_list')
        self.assertEqual(listed['current_session_id'], second['id'])
        self.assertEqual({s['id'] for s in listed['sessions']}, {first['id'], second['id']})
        bound = self.jobs.get(queued['id'])
        self.assertEqual(bound['logical_session_id'], first['id'])
        await manage('session_archive', session_id=first['id'])
        with self.assertRaises(ValueError):
            await self.plugin.dsh_chat(event, 'New message', agent['id'], session_id=first['id'])
        restored = (await manage('session_select', session_id=first['id']))['session']
        self.assertEqual(restored['status'], 'active')
        original = roster.get(agent['id'])
        self.assertEqual(original['version'], 1)
        self.assertEqual(original['profiles'][-1]['persona'], 'Original persona.')
        self.assertEqual(len(self.jobs.list()), 1)
        self.plugin.start_dsh_chat_worker.assert_called_once()

    async def test_meeting_side_chat_is_internal_but_explicit_owner_reply_still_delivers(self):
        roster = DshRoster(self.plugin.store, SCOPE)
        agents = [roster.create(name, "Persona") for name in ("甲", "乙")]
        DshMeetings(self.plugin.store, SCOPE).create(
            "闲聊", [roster.snapshot(agent["id"]) for agent in agents], "background", 1, 0, 0, "test", style="casual")
        event = FakeEvent()
        internal = json.loads(await self.plugin.dsh_chat(event, "会议旁路发言", agents[0]["id"]))
        direct = json.loads(await self.plugin.dsh_chat(event, "主人明确等答复", agents[1]["id"], report_to_owner=True))
        followup = FakeEvent()
        followup._extra["df_dsh_chat_completion"] = direct["id"]
        cascade = json.loads(await self.plugin.dsh_chat(followup, "后台再问一句", agents[1]["id"], report_to_owner=True))
        self.assertEqual([r["report_to_owner"] for r in (internal, direct, cascade)], [False, True, False])
        for receipt in (internal, direct, cascade):
            row = self.jobs.get(receipt["id"])
            self.jobs._save({**row, "status": "completed", "result": "reply"})
        self.plugin.wake_dsh_chat = AsyncMock(return_value="summary")
        sender = types.SimpleNamespace(enqueue_text=AsyncMock(return_value={"fishhome_queue_id": "one"}))
        self.plugin.sender = lambda: sender
        self.plugin.qq_outbox = lambda *_: object()
        await self.plugin.dispatch_dsh_chats(SCOPE, "12345")
        sender.enqueue_text.assert_awaited_once()
        self.assertIn(direct["id"], sender.enqueue_text.await_args.args[2])

    async def test_explicit_meeting_close_queues_exactly_one_owner_message(self):
        self.plugin.team_notice = AsyncMock(return_value="散场报告")
        source = types.SimpleNamespace(enqueue_text=AsyncMock(return_value={"fishhome_queue_id": "one"}))
        self.plugin.sender = lambda: source
        self.plugin.qq_outbox = lambda *_: object()
        row = {"autonomous": False, "report_on_close": True, "transcript": [
            {"kind": "member", "agent_id": "one", "name": "甲", "round": 1, "text": "引用原文"}]}
        for kind in ("progress", "completed"):
            self.assertTrue(await self.plugin.deliver_meeting_notice(
                SCOPE, row, {"kind": kind, "notice_id": f"meeting:1:{kind}"}, lambda _: True))
        await self.plugin.deliver_meeting_notice(
            SCOPE, {**row, "report_on_close": False}, {"kind": "completed", "notice_id": "other"}, lambda _: True)
        source.enqueue_text.assert_awaited_once_with(SCOPE, "散场报告", "dsh-meeting:meeting:1:completed")
        self.assertEqual(self.plugin.team_notice.await_args_list[1].args[2]["opening_responses"][0]["text"], "引用原文")

    async def test_test_roster_mirrors_profiles_under_separate_persistent_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "cmd_config.json").write_text(json.dumps({"agent_runner": {"config": {"persona_id": "feiyu-v3.0"}}}))
            store = core.Store(data / "plugin_data" / "activity")
            try:
                roster = DshRoster(store, SCOPE)
                official = roster.create("乌檐", "正式设定版本一")
                [fixture] = roster.refresh_test_profiles()
                self.assertNotEqual(fixture, official["id"])
                self.assertEqual(DshRoster(store, SCOPE, current_mode=True).resolve("乌檐"), official["id"])
                (data / "cmd_config.json").write_text(json.dumps({"agent_runner": {"config": {"persona_id": "fish-home-v3.0"}}}))
                scoped = DshRoster(store, SCOPE, current_mode=True)
                self.assertEqual(scoped.resolve("乌檐"), fixture)
                with self.assertRaises(ValueError):
                    scoped.snapshot(official["id"])
                roster.update(official["id"], persona="正式设定版本二")
                self.assertEqual(roster.refresh_test_profiles(), [fixture])
                self.assertEqual(scoped.snapshot(fixture)["persona"], "正式设定版本二")
                self.assertEqual(scoped.snapshot(fixture)["version"], 2)
            finally:
                store.db.close()

    async def test_reply_is_kept_when_qq_offline_and_model_not_called_twice(self):
        row = self.jobs.enqueue('Question', '', 'source')
        row.update(status='completed', result='Full subordinate response')
        self.jobs._save(row)
        response = 'A' * 2200
        self.plugin.wake_dsh_chat = AsyncMock(return_value=response)
        sender = types.SimpleNamespace(enqueue_text=AsyncMock(side_effect=[
            OSError('QQ offline'), {'fishhome_queue_id': 'one'}, {'fishhome_queue_id': 'two'}]))
        self.plugin.sender = lambda: sender
        self.plugin.qq_outbox = lambda *_: object()
        await self.plugin.dispatch_dsh_chats(SCOPE, '12345')
        self.assertEqual(self.jobs.get(row['id'])['agent_reply'], response)
        self.assertEqual(self.jobs.get(row['id'])['delivery'], 'pending')
        await self.plugin.dispatch_dsh_chats(SCOPE, '12345')
        await self.plugin.dispatch_dsh_chats(SCOPE, '12345')
        self.plugin.wake_dsh_chat.assert_awaited_once()
        calls = sender.enqueue_text.await_args_list
        self.assertEqual(calls[0].args[2], calls[1].args[2])
        self.assertEqual(calls[1].args[1] + calls[2].args[1], response)
        self.assertEqual(self.jobs.get(row['id'])['delivery'], 'notified')
        self.assertEqual(self.jobs.get(row['id'])['result'], 'Full subordinate response')

    async def test_self_initiated_reply_returns_to_fish_without_qq_message(self):
        row = self.jobs.enqueue('Personal discussion', '', 'source', autonomous=True)
        row.update(status='completed', result='Subordinate viewpoint')
        self.jobs._save(row)
        self.plugin.wake_dsh_chat = AsyncMock(return_value='I have read it.')
        self.plugin.sender = Mock(side_effect=AssertionError('No owner message requested'))
        await self.plugin.dispatch_dsh_chats(SCOPE, '12345')
        self.assertEqual(self.jobs.get(row['id'])['notification']['delivery'], 'internal')
        self.plugin.sender.assert_not_called()

    async def test_owner_change_prevents_delivery_of_old_scope_reply(self):
        row = self.jobs.enqueue('Question', '', 'source')
        row.update(status='completed', result='Result')
        self.jobs._save(row)
        self.plugin.wake_dsh_chat = AsyncMock()
        self.config['owner_id'] = '67890'
        self.plugin.control.write_text(json.dumps(self.config))
        await self.plugin.dispatch_dsh_chats(SCOPE, '12345')
        self.plugin.wake_dsh_chat.assert_not_awaited()
