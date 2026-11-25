import asyncio
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deploy.desktop_manager.owner_chat import access
from plugin.astrbot_plugin_bili_activity.core import Store
from plugin.astrbot_plugin_bili_activity.dsh_chats import DshChats
from plugin.astrbot_plugin_bili_activity.dsh_roster import DshRoster, OwnerChatRoster
from plugin.astrbot_plugin_bili_activity.team_history import TeamHistory
from plugin.astrbot_plugin_bili_activity.team_memory import TeamMemory
from test_dsh_chats import Runner, SCOPE


class OwnerChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        data = Path(folder.name) / "data"
        self.activity = data / "plugin_data/astrbot_plugin_bili_activity"
        self.activity.mkdir(parents=True)
        (data / "cmd_config.json").write_text(
            '{"agent_runner":{"runner_type":"local","config":{"persona":{"persona_id":"feiyu-v3.0"}}}}',
            encoding="utf-8")

    async def test_owner_chat_reuses_partner_and_memory_but_has_independent_history_and_pointer(self):
        agreement = "我和主人约定把海图叫潮汐图。"
        with closing(Store(self.activity)) as store:
            fish_roster = DshRoster(store, SCOPE)
            agent = fish_roster.create("潮汐笔记", "同一个具名伙伴")["id"]
            fish = fish_roster.new_session(agent, "肥鱼的原会话")["id"]
            fish_roster.set_native_session(agent, fish, "fish-native", route="commission")
            fish_original = fish_roster.get(agent)
            owner = OwnerChatRoster(store, SCOPE)
            self.assertEqual(owner.list_sessions(agent), [])
            first = access(self.activity, SCOPE, body={"action":"new", "agent":agent,
                           "group":"formal", "title":"海图"})["id"]
            job = access(self.activity, SCOPE, ready=True, body={"action":"send", "agent":agent,
                         "group":"formal", "session":first, "text":"我们把海图叫潮汐图。",
                         "request_id":"a" * 32})
            jobs = DshChats(store, SCOPE, owner_web=True)
            memory = TeamMemory(store, SimpleNamespace(), self.activity)
            backend = SimpleNamespace(search=AsyncMock(return_value=[]), save=AsyncMock(return_value=35),
                                      get=AsyncMock(return_value={"content":agreement}))
            memory.runtime.backend = AsyncMock(return_value=backend)
            runner = Runner()
            runner.team_memory = (memory, SCOPE)
            lock = asyncio.Lock()
            replies = iter([f'好。<fishhome_memory>{{"remember":[{{"text":"{agreement}","kind":"fact"}}]}}</fishhome_memory>',
                            '我记得叫潮汐图。<fishhome_memory>{"remember":[]}</fishhome_memory>'])

            async def finish(client, turn):
                client.done.add(turn["request_id"])
                return next(replies)

            with patch("plugin.astrbot_plugin_bili_activity.dsh_chats._wait_for_turn", side_effect=finish):
                args = (lambda _: runner, lambda _: "workspace", lambda _: True, None, lambda *_: lock)
                await jobs.dispatch_next(*args)
                done = jobs.get(job["id"])
                self.assertEqual((done["result"], done["memory_saved"]), ("好。", [35]))
                recorded = backend.save.await_args.kwargs
                self.assertEqual(recorded["owner"], agent)
                self.assertEqual(recorded["actors"], ["owner", agent])
                self.assertEqual(recorded["speakers"], ["owner", agent])
                source = recorded["source_ref"]["ref"]
                self.assertTrue(source.startswith("owner-chat:"))
                self.assertEqual(TeamHistory(store, SCOPE, owner_web=True).get(source, agent)["source"]["sender"], "owner")
                with self.assertRaises(ValueError):
                    TeamHistory(store, SCOPE).get(source, agent)
                self.assertEqual(memory.runtime.backend.await_args.args, (agent,))
                second = owner.new_session(agent, "第二份海图讨论")["id"]
                jobs.enqueue("之前叫什么？", agent, "b" * 32, session_id=second)
                await jobs.dispatch_next(*args)
            self.assertIn("主人这轮对你说", runner.prompts[0]["content"][0]["text"])
            self.assertNotIn("她这轮对你说", runner.prompts[0]["content"][0]["text"])
            self.assertNotEqual(runner.prompts[0]["sessionId"], runner.prompts[1]["sessionId"])
            self.assertEqual(fish_roster.get(agent), fish_original)
            self.assertEqual(DshChats(store, SCOPE).list(), [])
            with self.assertRaises(ValueError):
                owner.select_session(agent, fish)
            with self.assertRaises(ValueError):
                fish_roster.select_session(agent, first)
            readback = access(self.activity, SCOPE, query={"agent":agent, "session":first})
            self.assertEqual([line["result"] for line in readback["turns"]], ["好。"])
            self.assertEqual(owner.get(agent)["current_chat_session"], second)

    def test_formal_view_cannot_reuse_a_test_agent_or_other_persons_session(self):
        with closing(Store(self.activity)) as store:
            roster = DshRoster(store, SCOPE)
            test_agent = roster.create("测试伙伴", "测试人格", test_template="source")["id"]
            first = roster.create("伙伴一", "人格")["id"]
            second = roster.create("伙伴二", "人格")["id"]
            session = OwnerChatRoster(store, SCOPE).new_session(first)["id"]
        with self.assertRaises(ValueError):
            access(self.activity, SCOPE, query={"agent":test_agent})
        with self.assertRaises(ValueError):
            access(self.activity, SCOPE, body={"action":"select", "agent":second,
                                               "group":"formal", "session":session})
        self.assertEqual({agent["id"] for agent in access(self.activity, SCOPE, query={})["agents"]}, {first, second})
