import tempfile
import unittest
from contextlib import closing

from test_bili_activity import core
from bili_activity_test.team_history import TeamHistory


class TeamHistoryTests(unittest.TestCase):
    def test_source_facts_filter_scope_and_leave_store_unchanged(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            scope = 'qq:FriendMessage:123'
            a, b = 'a' * 16, 'b' * 16
            chat, meeting, task, public = '1' * 16, '2' * 16, '3' * 12, '4' * 12
            def put(prefix, ident, row, owner=scope):
                store.setting(f'{prefix}:v1:{owner}:{ident}', row)
            long_text = '原文' * 1000
            put('dsh-chat', chat, {'id': chat, 'agent_id': a, 'status': 'completed', 'message': long_text,
                                   'result': long_text, 'session_id': 'session', 'persona_version': 2, 'created': 1})
            put('dsh-meeting', meeting, {'id': meeting, 'status': 'completed', 'members': {a: {'id': a}, b: {'id': b}},
                                          'transcript': [{'kind': 'host', 'text': '主持原文'},
                                                         {'kind': 'member', 'agent_id': b, 'text': long_text}],
                                          'summary': '会议摘要', 'created': 2})
            put('dsh-roster', a, {'id': a, 'profiles': [{'version': 1, 'persona': long_text, 'name': '旧名', 'at': 3},
                                                        {'version': 2, 'persona': '新设定', 'name': '新名', 'at': 4}]})
            put('commissioned-file', task, {'id': task, 'state': 'completed', 'agent': {'id': a},
                                              'instruction': '任务指令', 'result': '实际成果', 'report': 'output/full.md', 'created': 5})
            put('commissioned-file', public, {'id': public, 'job_kind': 'web', 'state': 'completed',
                'spec': {'agent': {'id': b}, 'query': '资料'}, 'result': '资料结果', 'agent_reply': '鱼写的评价', 'created': 6})
            put('commissioned-file', '5' * 12, {'state': 'running', 'instruction': '未完成的指令', 'created': 7})
            put('dsh-chat', '6' * 16, {'agent_id': a, 'status': 'completed', 'result': '别人的秘密'}, owner=scope + ':other')
            before = list(store.db.execute('SELECT key,value FROM settings ORDER BY key'))
            history = TeamHistory(store, scope)
            self.assertEqual(len(history.list(limit=20)), 7)
            self.assertEqual(history.get('chat:' + chat)['source']['result'], long_text)
            self.assertEqual(history.get('meeting:' + meeting)['source']['transcript'][1]['text'], long_text)
            self.assertEqual(history.get(f'agent:{a}:1')['source']['profile']['persona'], long_text)
            self.assertEqual(history.get('public:' + public)['source']['agent_reply'], '鱼写的评价')
            self.assertEqual(history.get('task:' + task)['source']['report'], 'output/full.md')
            self.assertEqual({r['ref'] for r in history.search(agent_id=b)}, {'meeting:' + meeting, 'public:' + public})
            self.assertEqual(history.search('实际成果', kind='task')[0]['summary'], '实际成果')
            self.assertTrue(history.list(kind='chat')[0]['summary_is_excerpt'])
            self.assertEqual(history.list(query='未完成的指令')[0]['summary'], '')
            with self.assertRaises(ValueError):
                history.get('chat:' + chat, agent_id=b)
            with self.assertRaises(ValueError):
                history.get('chat:' + '6' * 16)
            self.assertEqual(before, list(store.db.execute('SELECT key,value FROM settings ORDER BY key')))


if __name__ == '__main__':
    unittest.main()
