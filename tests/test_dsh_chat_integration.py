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
