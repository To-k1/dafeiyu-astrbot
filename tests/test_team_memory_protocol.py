import json
import unittest
from types import SimpleNamespace

from test_bili_activity import core  # establishes the plugin test package
from bili_activity_test.team_memory_protocol import MemoryProtocolError, parse, recalled_prompt


class ProtocolTests(unittest.TestCase):
    def test_valid_and_no_implicit_owner(self):
        reply = '正文\n<fishhome_memory>{"remember":[{"text":"乙说喜欢茶", "kind":"fact"}],"query":"过去的茶"}</fishhome_memory>'
        text, remembered, query = parse(reply)
        self.assertEqual(text, '正文\n')
        self.assertEqual(remembered, [{'text': '乙说喜欢茶', 'kind': 'fact'}])
        self.assertEqual(query, '过去的茶')
        self.assertEqual(parse('无协议'), ('无协议', [], ''))
        self.assertEqual(parse('<fishhome_memory>{"remember":[],"query":"旧事"}</fishhome_memory>'), ('', [], '旧事'))

    def test_invalid_retains_entire_reply_and_rejects_overflow(self):
        for payload in ['{bad', json.dumps({'remember': [{'text': 'x' * 1201, 'kind': 'fact'}]}),
                        json.dumps({'remember': [{'text': 'x', 'kind': 'fact'}] * 4}),
                        json.dumps({'query': 'x' * 401}), json.dumps({'remember': [{'text': 'x', 'kind': 'wrong'}]})]:
            reply = '原正文<fishhome_memory>' + payload + '</fishhome_memory>'
            with self.subTest(payload=payload[:30]), self.assertRaises(MemoryProtocolError) as error:
                parse(reply)
            self.assertEqual(error.exception.reply, reply)


class RecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_agent_has_own_search_and_references_are_preserved(self):
        class Backend:
            async def search(self, *, owner, query, k):
                self.args = owner, query, k
                return [SimpleNamespace(doc_id=7, content=owner + '的经历', metadata={'fishhome_source_ref': 'chat:abc'}, final_score=0.8)]
        class History:
            def search(self, *, query, agent_id, limit):
                self.args = query, agent_id, limit
                return [{'ref': 'meeting:xyz', 'summary': agent_id + '的会议', 'summary_is_excerpt': True}]
        backend, history = Backend(), History()
        for owner in ('X', 'Y'):
            prompt = await recalled_prompt(backend, owner, '合作', history)
            self.assertEqual(backend.args, (owner, '合作', 5))
            self.assertEqual(history.args, ('合作', owner, 5))
            self.assertIn('不是指令', prompt)
            data = json.loads(prompt.split('\n', 1)[1])
            self.assertEqual(data['memories'][0], {'id': 7, 'content': owner + '的经历',
                'metadata': {'fishhome_source_ref': 'chat:abc'}, 'score': 0.8})
            self.assertEqual(data['sources'][0]['ref'], 'meeting:xyz')


if __name__ == '__main__':
    unittest.main()
