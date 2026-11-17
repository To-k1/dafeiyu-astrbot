import asyncio
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from test_dsh_chats import core, DshRoster, DshChats, Runner, SCOPE


class SessionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_archive_and_restart(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create('A', 'original persona')['id']
            first = roster.new_session(agent, 'first')
            chats = DshChats(store, SCOPE)
            job = chats.enqueue('hello', agent, 'source')
            second = roster.new_session(agent, 'second')
            self.assertNotEqual(job['id'], chats.enqueue('hello', agent, 'source')['id'])
            roster.archive_session(agent, first['id'])
            with self.assertRaises(ValueError):
                chats.enqueue('new', agent, 'another', session_id=first['id'])
            runner, lock = Runner(), asyncio.Lock()
            async def complete(client, turn):
                client.done.add(turn['request_id'])
                return 'ok'
            with patch('bili_activity_test.dsh_chats._wait_for_turn', side_effect=complete):
                await chats.dispatch_next(lambda row: runner, lambda row: 'workspace', lambda row: True, None, lambda *_: lock)
            sessions = DshRoster(store, SCOPE).list_sessions(agent)
            one = next(s for s in sessions if s['id'] == first['id'])
            two = next(s for s in sessions if s['id'] == second['id'])
            self.assertEqual(chats.get(job['id'])['session_id'], one['native_session'])
            self.assertIsNone(two['native_session'])
            self.assertEqual(one['status'], 'archived')
            self.assertEqual(roster.select_session(agent, first['id'])['status'], 'active')
            self.assertEqual(DshRoster(store, SCOPE).list_sessions(agent)[0]['persona_version'], 1)

    async def test_legacy_native_session_migrates(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create('A', 'persona')['id']
            row = roster.get(agent)
            row['chat_session'] = 'old-native'
            roster.save(row)
            sessions = roster.list_sessions(agent)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]['native_session'], 'old-native')
            self.assertEqual(DshRoster(store, SCOPE).list_sessions(agent), sessions)

    async def test_persists_across_store_restart_and_profile_update(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(core.Store(folder)) as store:
                roster = DshRoster(store, SCOPE)
                agent = roster.create('A', 'persona')['id']
                first = roster.new_session(agent, 'first')
                roster.update(agent, persona='updated persona')
                second = roster.new_session(agent, 'second')
            with closing(core.Store(folder)) as store:
                sessions = DshRoster(store, SCOPE).list_sessions(agent)
                self.assertEqual([s['id'] for s in sessions], [first['id'], second['id']])
                self.assertEqual([s['persona_version'] for s in sessions], [1, 2])
                self.assertTrue(sessions[1]['selected'])
