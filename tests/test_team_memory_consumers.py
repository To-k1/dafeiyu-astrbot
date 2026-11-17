import copy
import tempfile
import unittest
from contextlib import closing
from unittest.mock import AsyncMock, patch

from test_bili_activity import core
from test_dsh_meetings import PROFILES, SCOPE, MeetingRunner
from bili_activity_test.dsh_meetings import DshMeetings
from bili_activity_test.dsh_task import execute_file_task
from bili_activity_test.team_memory import TeamMemory


class Memory:
    decode = staticmethod(TeamMemory.decode)

    def __init__(self):
        self.prepared = []

    async def prepare(self, scope, agent, query):
        self.prepared.append((scope, agent, query))
        return 'RECALL-' + agent


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_meeting_member_gets_own_recall_and_proposals(self):
        runner, memory = MeetingRunner(), Memory()
        runner.team_memory = (memory, SCOPE)
        async def reply(client, turn, *args):
            return '{"position":"结论"}<fishhome_memory>{"remember":[{"text":"合作经验","kind":"fact"}],"query":""}</fishhome_memory>'
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meeting = DshMeetings(store, SCOPE)
            ident = meeting.create('合作议题', PROFILES, 'background', 1, 0, 0, 'msg')['id']
            # Existing runner supplies receipts and request history; only final text is replaced.
            with patch('bili_activity_test.dsh_meetings._wait_for_turn', side_effect=reply):
                await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
            row = meeting.get(ident)
            self.assertEqual(row['status'], 'completed')
            self.assertEqual({x[1] for x in memory.prepared}, {p['id'] for p in PROFILES})
            for entry in row['transcript']:
                self.assertEqual(entry['participant_record']['position'], '结论')
                self.assertNotIn('fishhome_memory', entry['text'])
                self.assertEqual(entry['memory_proposals'][0]['text'], '合作经验')

            for _, _, prompt in runner.sent:
                self.assertEqual(sum('RECALL-' + p['id'] in prompt for p in PROFILES), 1)

    async def test_recall_failure_is_recorded_without_blocking_meeting(self):
        class BrokenMemory(Memory):
            async def prepare(self, scope, agent, query):
                raise RuntimeError("backend unavailable")
        runner = MeetingRunner()
        runner.team_memory = (BrokenMemory(), SCOPE)
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meeting = DshMeetings(store, SCOPE)
            ident = meeting.create('议题', PROFILES, 'background', 1, 0, 0, 'broken')['id']
            await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
            row = meeting.get(ident)
            self.assertEqual(row['status'], 'completed')
            self.assertEqual([entry['memory_error']['stage'] for entry in row['transcript']], ['recall', 'recall'])

    async def test_file_task_recall_and_proposal_leave_artifact_authoritative(self):
        memory = Memory()
        class Client:
            model_route = 'go'
            team_memory = (memory, SCOPE)
            async def call(self, method, args):
                return {'values': {'tokenUsage': {'input': 1}}}
            async def go_receipts(self, session):
                return {'receipts': [1]}
        task = {'source': None, 'cwd': r'C:\\work', 'state': 'prepared', 'billing': 'interaction',
                'instruction': '写报告', 'request_id': 'req', 'report': 'output/a.md', 'sources': 'output/a.json',
                'agent': {'id': PROFILES[0]['id'], 'name': '甲', 'version': 1, 'persona': '人格', 'guides': []}}
        saved = []
        with patch('bili_activity_test.dsh_task._open_session', new=AsyncMock(return_value=('session', 'go'))), \
             patch('bili_activity_test.dsh_task._prompt', new=AsyncMock()) as prompt, \
             patch('bili_activity_test.dsh_task._wait_for_turn', new=AsyncMock(return_value='回复<fishhome_memory>{"remember":[{"text":"工作经验","kind":"fact"}]}</fishhome_memory>')), \
             patch('bili_activity_test.dsh_task._read_text', new=AsyncMock(side_effect=['真实报告', '{"source":null}'])):
            report, usage = await execute_file_task(Client(), task, lambda value: saved.append(copy.deepcopy(value)), lambda: True)
        self.assertEqual(report, '真实报告')
        self.assertIn('RECALL-' + PROFILES[0]['id'], prompt.await_args.args[2])
        self.assertEqual(task['memory_proposals'][0]['text'], '工作经验')
        self.assertTrue(any(s.get('memory_proposals') for s in saved))
        self.assertEqual(usage['billing_source'], 'go')

    async def test_file_query_continuation_is_persisted_and_polled_after_restart(self):
        memory = Memory()
        class Client:
            model_route = 'go'
            team_memory = (memory, SCOPE)
            async def call(self, method, args):
                return {'values': {'tokenUsage': {'input': 1}}}
            async def go_receipts(self, session):
                return {'receipts': [1, 2]}
        task = {'source': None, 'cwd': r'C:\\work', 'state': 'prepared', 'billing': 'interaction',
                'instruction': '写报告', 'request_id': 'original', 'report': 'output/a.md', 'sources': 'output/a.json',
                'agent': {'id': PROFILES[0]['id'], 'name': '甲', 'version': 1, 'persona': '人格', 'guides': []}}
        snapshots = []
        first = '<fishhome_memory>{"remember":[{"text":"第一条","kind":"fact"}],"query":"旧事"}</fishhome_memory>'
        with patch('bili_activity_test.dsh_task._open_session', new=AsyncMock(return_value=('session', 'go'))) as opened, \
             patch('bili_activity_test.dsh_task._prompt', new=AsyncMock()) as prompt, \
             patch('bili_activity_test.dsh_task._wait_for_turn', new=AsyncMock(side_effect=[first, TimeoutError(), '完成'])) as wait, \
             patch('bili_activity_test.dsh_task._read_text', new=AsyncMock(side_effect=['真实报告', '{"source":null}'])):
            with self.assertRaises(TimeoutError):
                await execute_file_task(Client(), task, lambda v: snapshots.append(copy.deepcopy(v)), lambda: True)
            persisted = snapshots[-1]
            continuation = persisted['request_id']
            self.assertNotEqual(continuation, 'original')
            self.assertEqual(persisted['memory_queries'][0]['continuation_request_id'], continuation)
            self.assertEqual(persisted['memory_queries'][0]['status'], 'resolved')
            report, _ = await execute_file_task(Client(), persisted, lambda v: snapshots.append(copy.deepcopy(v)), lambda: True)
        self.assertEqual(report, '真实报告')
        self.assertEqual(opened.await_count, 1)
        self.assertEqual(prompt.await_count, 2)
        self.assertEqual(wait.await_args.args[1]['request_id'], continuation)
        self.assertEqual(persisted['memory_proposals'][0]['text'], '第一条')

    async def test_file_quota_retry_resumes_query_only(self):
        from bili_activity_test.dsh_task import GoQuotaWaiting, reset_unstarted_go_task
        memory = Memory()
        class Client:
            model_route = 'go'
            team_memory = (memory, SCOPE)
            async def call(self, method, args):
                return {'values': {'tokenUsage': {'input': 1}}}
            async def go_receipts(self, session):
                return {'receipts': [1, 2]}
        task = {'source': None, 'cwd': r'C:\\work', 'state': 'prepared', 'billing': 'interaction',
                'instruction': '写报告', 'request_id': 'original', 'report': 'output/a.md', 'sources': 'output/a.json',
                'agent': {'id': PROFILES[0]['id'], 'name': '甲', 'version': 1, 'persona': '人格', 'guides': []}}
        reply = '<fishhome_memory>{"remember":[{"text":"经验","kind":"fact"}],"query":"旧事"}</fishhome_memory>'
        saved = []
        with patch('bili_activity_test.dsh_task._open_session', new=AsyncMock(return_value=('session', 'go'))) as opened, \
             patch('bili_activity_test.dsh_task._prompt', new=AsyncMock()) as prompt, \
             patch('bili_activity_test.dsh_task._wait_for_turn', new=AsyncMock(side_effect=[reply, GoQuotaWaiting(), '完成'])), \
             patch('bili_activity_test.dsh_task._read_text', new=AsyncMock(side_effect=['报告', '{"source":null}'])):
            with self.assertRaises(GoQuotaWaiting):
                await execute_file_task(Client(), task, lambda v: saved.append(copy.deepcopy(v)), lambda: True)
            resumed = saved[-1]
            old_request = resumed['request_id']
            resumed['state'] = 'go_wait'
            reset_unstarted_go_task(resumed)
            self.assertEqual(resumed['state'], 'memory_continue')
            self.assertNotEqual(resumed['request_id'], old_request)
            await execute_file_task(Client(), resumed, lambda v: saved.append(copy.deepcopy(v)), lambda: True)
        self.assertEqual(opened.await_count, 1)
        self.assertEqual(prompt.await_count, 3)
        self.assertIn('只读历史结果', prompt.await_args.args[2])
        self.assertEqual(resumed['memory_proposals'][0]['text'], '经验')

    async def test_query_failure_leaves_pending_and_never_reports_success(self):
        class BrokenQuery(Memory):
            async def prepare(self, scope, agent, query):
                if query == '旧事':
                    raise RuntimeError('unavailable')
                return await super().prepare(scope, agent, query)
        memory = BrokenQuery()
        class Client:
            model_route = 'go'
            team_memory = (memory, SCOPE)
        task = {'source': None, 'cwd': r'C:\\work', 'state': 'prepared', 'billing': 'interaction',
                'instruction': '写报告', 'request_id': 'original', 'report': 'output/a.md', 'sources': 'output/a.json',
                'agent': {'id': PROFILES[0]['id'], 'name': '甲', 'version': 1, 'persona': '人格', 'guides': []}}
        saved = []
        with patch('bili_activity_test.dsh_task._open_session', new=AsyncMock(return_value=('session', 'go'))), \
             patch('bili_activity_test.dsh_task._prompt', new=AsyncMock()) as prompt, \
             patch('bili_activity_test.dsh_task._wait_for_turn', new=AsyncMock(return_value='<fishhome_memory>{"query":"旧事"}</fishhome_memory>')):
            with self.assertRaisesRegex(Exception, '主动历史查询失败'):
                await execute_file_task(Client(), task, lambda v: saved.append(copy.deepcopy(v)), lambda: True)
        self.assertEqual(prompt.await_count, 1)
        self.assertEqual(saved[-1]['memory_query']['status'], 'pending')
        self.assertEqual(saved[-1]['memory_error']['stage'], 'query')
        self.assertEqual(saved[-1]['memory_queries'], [])

    async def test_meeting_quota_retry_keeps_member_query_turn(self):
        from bili_activity_test.dsh_task import GoQuotaWaiting
        memory, runner = Memory(), MeetingRunner()
        runner.team_memory = (memory, SCOPE)
        runner.go_quota_available = AsyncMock(return_value=True)
        first_session, query_sent, quota_sent = None, False, False
        async def wait(client, turn, *args):
            nonlocal first_session, query_sent, quota_sent
            if first_session is None:
                first_session = turn['session_id']
            if turn['session_id'] == first_session and not query_sent:
                query_sent = True
                return '<fishhome_memory>{"remember":[{"text":"经验","kind":"fact"}],"query":"旧事"}</fishhome_memory>'
            if turn['session_id'] == first_session and not quota_sent:
                quota_sent = True
                raise GoQuotaWaiting()
            return '{"position":"最终"}'
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meeting = DshMeetings(store, SCOPE)
            ident = meeting.create('议题', PROFILES, 'background', 1, 0, 0, 'quota')['id']
            with patch('bili_activity_test.dsh_meetings._wait_for_turn', side_effect=wait):
                await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
                mid = meeting._get(ident)
                self.assertEqual(mid['status'], 'go_wait')
                turn = next(value for value in mid['batch']['turns'].values() if value.get('memory_retry'))
                session = turn['session_id']
                await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
            row = meeting.get(ident)
            self.assertEqual(row['status'], 'completed')
            member = next(entry for entry in row['transcript'] if entry.get('memory_queries'))
            self.assertEqual(member['session_id'], session)
            self.assertEqual(member['memory_proposals'][0]['text'], '经验')
            prompts = [text for sid, _, text in runner.sent if sid == session]
            self.assertEqual(sum('你请求的只读历史结果' not in text for text in prompts), 1)
            self.assertEqual(sum('你请求的只读历史结果' in text for text in prompts), 2)

    async def test_meeting_query_is_persisted_and_recovered_without_repeating(self):
        from bili_activity_test.dsh_task import _prompt as real_prompt
        memory, runner = Memory(), MeetingRunner()
        runner.team_memory = (memory, SCOPE)
        query_session = None
        interrupted = False
        async def send(client, turn, text):
            nonlocal interrupted
            await real_prompt(client, turn, text)
            if '你请求的只读历史结果' in text and not interrupted:
                interrupted = True
                raise TimeoutError()
        async def wait(client, turn, *args):
            nonlocal query_session
            session = turn['session_id']
            if query_session is None:
                query_session = session
                return '<fishhome_memory>{"remember":[{"text":"记下","kind":"fact"}],"query":"历史"}</fishhome_memory>'
            return '{"position":"最终结论"}'
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            meeting = DshMeetings(store, SCOPE)
            ident = meeting.create('议题', PROFILES, 'background', 1, 0, 0, 'query')['id']
            with patch('bili_activity_test.dsh_meetings._prompt', side_effect=send), \
                 patch('bili_activity_test.dsh_meetings._wait_for_turn', side_effect=wait):
                await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
                mid = meeting._get(ident)
                active = next(turn for turn in mid['batch']['turns'].values() if turn.get('memory_queries'))
                continuation = active['request_id']
                self.assertEqual(active['memory_queries'][0]['continuation_request_id'], continuation)
                await meeting.dispatch_next(lambda _: runner, lambda _: 'workspace', lambda _: True, None)
            row = meeting.get(ident)
            self.assertEqual(row['status'], 'completed')
            member = next(entry for entry in row['transcript'] if entry.get('memory_queries'))
            self.assertEqual(member['request_id'], continuation)
            self.assertEqual(member['memory_proposals'][0]['text'], '记下')
            self.assertEqual(member['go_receipt_count'], 2)
            self.assertEqual(sum(request == continuation for _, request, _ in runner.sent), 1)


if __name__ == '__main__':
    unittest.main()
