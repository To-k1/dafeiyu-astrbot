import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_bili_activity import core
from bili_activity_test.team_history import TeamHistory
from bili_activity_test.commissioned import CommissionedFiles
from bili_activity_test.dsh_meetings import DshMeetings
from bili_activity_test.team_mode import latest_team_notice


class TeamHistoryTests(unittest.TestCase):
    def test_live_meeting_and_task_lookups_follow_the_configured_persona(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            with closing(core.Store(data / "plugin_data" / "astrbot_plugin_bili_activity")) as store:
                def select(persona):
                    (data / "cmd_config.json").write_text(json.dumps(
                        {"agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": persona}}}}))
                scope, owner = "qq:FriendMessage:123", "123"
                profiles = [{"id": n * 16, "name": n, "persona": "text", "version": 1} for n in "ab"]
                plan = {"source": "test.csv", "cwd": r"D:\shared", "instruction": "read"}
                old_meeting = DshMeetings(store, scope).create("old", profiles, "background", 1, 0, 0, "same")
                old_task = CommissionedFiles(store).accept(scope, owner, "same", plan)
                store.setting("dsh-team-latest:" + scope, {"reference": old_meeting["id"], "summary": "old meeting"})
                select("feiyu-v3.0")
                history = TeamHistory(store, scope)
                self.assertEqual(CommissionedFiles(store).list(scope, owner), [])
                self.assertEqual(DshMeetings(store, scope).list(), [])
                self.assertEqual(history.list(), [])
                self.assertIsNone(latest_team_notice(store, scope))
                self.assertFalse(CommissionedFiles(store).cancel(scope, owner, old_task))
                with self.assertRaises(ValueError):
                    DshMeetings(store, scope).get(old_meeting["id"])
                new_meeting = DshMeetings(store, scope).create("new", profiles, "background", 1, 0, 0, "same")
                new_task = CommissionedFiles(store).accept(scope, owner, "same", plan)
                self.assertNotEqual(new_task, old_task)
                self.assertEqual(history.get("meeting:" + new_meeting["id"])["source"]["persona_mode"], "formal")
                select("fish-home-v3.0")
                self.assertEqual([row["id"] for row in CommissionedFiles(store).list(scope, owner)], [old_task])
                self.assertEqual([row["id"] for row in DshMeetings(store, scope).list()], [old_meeting["id"]])
                self.assertEqual(latest_team_notice(store, scope)["summary"], "old meeting")

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
