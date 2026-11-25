import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugin.astrbot_plugin_bili_activity.qq_conversations import (
    CHAT_SLOT,
    conversation_command,
    restore_persona_chat,
)


class QQConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        def chat(cid, persona_id, updated_at, history="[{}]"):
            return SimpleNamespace(cid=cid, persona_id=persona_id, title="Topic",
                                   updated_at=updated_at, created_at=updated_at, history=history)

        self.chats = [
            chat("aaaa1111", "feiyu-v3.0", 1),
            chat("aaaa2222", "feiyu-v3.0", 5),
            chat("bbbb1111", "fish-home-v3.0", 3),
            chat("bbbb2222", "fish-home-v3.0", 4, history="[]"),
            chat("cccc1111", None, 6),
        ]
        self.selected = "aaaa1111"
        self.slots = {}

        async def switch(_scope, cid):
            self.selected = cid

        async def put(_scope, key, value):
            self.slots[key] = value

        self.manager = SimpleNamespace(get_conversations=AsyncMock(return_value=self.chats),
                                       get_curr_conversation_id=AsyncMock(side_effect=lambda _: self.selected),
                                       switch_conversation=AsyncMock(side_effect=switch), new_conversation=AsyncMock())
        self.prefs = SimpleNamespace(session_put=AsyncMock(side_effect=put),
                                     session_get=AsyncMock(side_effect=lambda _, key, default: self.slots.get(key, default)))

    async def test_switching_personas_recovers_last_selection_with_no_new_history(self):
        old = await restore_persona_chat(self.manager, self.prefs, "owner", "feiyu-v3.0", "fish-home-v3.0")
        self.assertEqual((old, self.selected), ("aaaa1111", "bbbb1111"))
        self.assertEqual(self.slots[CHAT_SLOT + "feiyu-v3.0"], "aaaa1111")
        await restore_persona_chat(self.manager, self.prefs, "owner", "fish-home-v3.0", "feiyu-v3.0")
        self.assertEqual(self.selected, "aaaa1111")
        self.manager.new_conversation.assert_not_awaited()

    async def test_saved_wrong_mode_is_rejected_and_first_use_creates_only_its_own_chat(self):
        self.slots[CHAT_SLOT + "fish-home-v4.0"] = "aaaa1111"
        await restore_persona_chat(self.manager, self.prefs, "owner", "feiyu-v3.0", "fish-home-v4.0")
        self.manager.switch_conversation.assert_not_awaited()
        self.manager.new_conversation.assert_awaited_once_with("owner", content=[], persona_id="fish-home-v4.0")

    async def test_owner_command_lists_only_its_group_and_needs_unique_id(self):
        result = await conversation_command(self.manager, "owner", "formal", "/对话")
        self.assertIn("aaaa1111（当前）", result)
        self.assertNotIn("bbbb1111", result)
        self.assertNotIn("cccc1111", result)
        for forbidden in ("aaaa", "bbbb1111", "cccc1111"):
            await conversation_command(self.manager, "owner", "formal", "/对话 切换 " + forbidden)
        self.manager.switch_conversation.assert_not_awaited()
        await conversation_command(self.manager, "owner", "formal", "/对话 切换 aaaa222")
        self.manager.switch_conversation.assert_awaited_once_with("owner", "aaaa2222")
