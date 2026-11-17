"""Real queue and source wiring, with model/embedding calls replaced locally."""
import asyncio
import json
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_dsh_chats import core, DshRoster, DshChats, Runner, SCOPE
from bili_activity_test.team_memory import TeamMemory
from bili_activity_test.team_memory_protocol import GUIDANCE


class MemoryWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_wait_resumes_query_instead_of_resending_original_question(self):
        from bili_activity_test.dsh_task import GoQuotaWaiting
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            agent = DshRoster(store, SCOPE).create('A', 'persona')['id']
            service = TeamMemory(store, SimpleNamespace(), folder)
            service.prepare = AsyncMock(return_value=GUIDANCE + '\n历史查询结果')
            runner = Runner()
            runner.team_memory = (service, SCOPE)
            jobs = DshChats(store, SCOPE)
            job = jobs.enqueue('只发一次的原问题', agent, 'source')
            replies = iter(['<fishhome_memory>{"remember":[{"text":"已有约定","kind":"fact"}],"query":"约定"}</fishhome_memory>',
                            GoQuotaWaiting('quota'), '已查到约定。'])
            async def wait(client, turn):
                result = next(replies)
                if isinstance(result, Exception):
                    raise result
                client.done.add(turn['request_id'])
                return result
            lock = asyncio.Lock()
            args = (lambda _: runner, lambda _: 'workspace', lambda _: True, None, lambda *_: lock)
            with patch('bili_activity_test.dsh_chats._wait_for_turn', side_effect=wait):
                for _ in range(3):
                    await jobs.dispatch_next(*args)
            row = jobs.get(job['id'])
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(len(row['memory_proposals']), 1)
            self.assertEqual(row['go_receipt_count'], 2)
            texts = [p['content'][0]['text'] for p in runner.prompts]
            self.assertEqual(texts[1], texts[2])
            self.assertNotIn('她这轮对你说', texts[2])

    async def test_agent_active_query_stays_in_original_session_then_keeps_agreement(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create('First', 'Original persona')['id']
            service = TeamMemory(store, SimpleNamespace(), folder)
            service.prepare = AsyncMock(return_value=GUIDANCE + '\n来自旧会议的约定：先核实来源。')
            client = Runner()
            client.team_memory = (service, SCOPE)
            jobs = DshChats(store, SCOPE)
            job = jobs.enqueue('我们以前约定了什么？', agent, 'one')
            replies = iter([
                '<fishhome_memory>{"query":"以前的约定","remember":[]}</fishhome_memory>',
                '我们约定先核实来源。<fishhome_memory>{"remember":[{"text":"我与肥鱼约定先核实来源，原会议可回查。","kind":"fact"}]}</fishhome_memory>'])
            async def complete(runner, turn):
                runner.done.add(turn['request_id'])
                return next(replies)
            lock = asyncio.Lock()
            args = (lambda _: client, lambda _: 'workspace', lambda _: True, None, lambda *_: lock)
            with patch('bili_activity_test.dsh_chats._wait_for_turn', side_effect=complete):
                await jobs.dispatch_next(*args)
                self.assertEqual(jobs.get(job['id'])['status'], 'running')
                await jobs.dispatch_next(*args)
            result = jobs.get(job['id'])
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['result'], '我们约定先核实来源。')
            self.assertEqual(result['go_receipt_count'], 2)
            self.assertEqual(len({p['sessionId'] for p in client.prompts}), 1)
            self.assertEqual(service.prepare.await_args.args[1], agent)
            self.assertEqual(len(result['memory_proposals']), 1)
            backend = SimpleNamespace(save=AsyncMock(return_value=41))
            service.runtime.backend = AsyncMock(return_value=backend)
            await service.remember(SCOPE, agent, 'chat:' + job['id'], result['memory_proposals'])
            saved = backend.save.await_args.kwargs
            self.assertEqual(saved['owner'], agent)
            self.assertEqual(saved['speakers'], [])
            self.assertEqual(saved['summary_author'], agent)
            self.assertEqual(saved['source_ref']['ref'], 'chat:' + job['id'])
            self.assertNotIn('主人', saved['actors'])

    async def test_fish_event_has_source_and_no_invented_speaker_or_preference(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            roster = DshRoster(store, SCOPE)
            agent = roster.create('Second', 'A persona')['id']
            jobs = DshChats(store, SCOPE)
            job = jobs.enqueue('讨论X', agent, 'two')
            row = jobs.get(job['id'])
            row.update(status='completed', result='小弟说：我喜欢X。')
            jobs._save(row)
            service = TeamMemory(store, SimpleNamespace(), folder)
            backend = SimpleNamespace(save=AsyncMock(return_value=42))
            service.runtime.backend = AsyncMock(return_value=backend)
            service.runtime.fish_scope = AsyncMock(return_value={'session_id': SCOPE, 'persona_id': 'P', 'adapter_id': 'qq'})
            event = SimpleNamespace(unified_msg_origin=SCOPE)
            ref = 'chat:' + job['id']
            await service.record_events(event, [ref])
            saved = backend.save.await_args.kwargs
            self.assertEqual(saved['owner'], 'fish')
            self.assertEqual(saved['speakers'], [])
            self.assertIsNone(saved['evaluation_author'])
            self.assertNotIn('我喜欢X', saved['content'])
            self.assertIn(ref, saved['content'])
            await service.operate(event, action='save', source_ref=ref, content='我认为这次分歧有用。', kind='evaluation')
            saved = backend.save.await_args.kwargs
            self.assertEqual(saved['evaluation_author'], 'fish')
            self.assertNotEqual(saved['source_ref'].get('view'), 'event')
            other = roster.create('Third', 'Another persona')['id']
            with self.assertRaises(ValueError):
                await service.operate(event, action='save', agent_id=other, source_ref=ref, content='不属于我的经历')

    async def test_proposals_have_distinct_source_ids_and_kind(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            agent = DshRoster(store, SCOPE).create('member', 'persona')['id']
            jobs = DshChats(store, SCOPE)
            row = jobs.enqueue('message', agent, 'key')
            row.update(status='completed', result='done')
            jobs._save(row)
            service = TeamMemory(store, SimpleNamespace(), folder)
            backend = SimpleNamespace(save=AsyncMock(side_effect=[1, 2]))
            service.runtime.backend = AsyncMock(return_value=backend)
            await service.remember(SCOPE, agent, 'chat:' + row['id'], [
                {'kind': 'fact', 'text': 'fact'}, {'kind': 'evaluation', 'text': 'evaluation'}])
            a, b = [x.kwargs for x in backend.save.await_args_list]
            self.assertNotEqual(a['source_ref'], b['source_ref'])
            self.assertIsNone(a['evaluation_author'])
            self.assertEqual(b['evaluation_author'], agent)
            self.assertEqual(a['speakers'], [])

    def test_native_search_result_has_id_and_score(self):
        self.assertEqual(TeamMemory.document(SimpleNamespace(doc_id=12, final_score=.8, content='x', metadata={})),
                         {'id': 12, 'score': .8, 'content': 'x', 'metadata': {}})
