import json
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_bili_activity import FakeEvent, OTHER_SCOPE, SCOPE, core, main
from bili_activity_test import memewiki, team_delegation


class MemeWikiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.plugin = main.BiliActivity.__new__(main.BiliActivity)
        self.plugin.store = core.Store(folder)
        self.addCleanup(self.plugin.store.close)
        self.plugin.control = Path(folder, "control.json")
        self.plugin.control.write_text(json.dumps({"owner_scope": SCOPE, "owner_id": "12345", "enabled": True}))
        self.plugin.life_runs = set()
        self.path = Path(folder, memewiki.FILENAME)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE memes (title, aliases, summary, usage, origin, tags, slug)")
            db.executemany("INSERT INTO memes VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("倒着领先", "dzlx,反向领先", "用领先形容落后。", "常见于自嘲和反讽。", "来自一次聊天。", "反讽", "reverse"),
                ("倒着领先奖", "", "相关说法。", "", "", "", ""),
                ("甲版本", "同名", "第一个义项。", "", "", "", ""),
                ("乙版本", "同名", "第二个义项。", "", "", "", ""),
            ])

    async def test_owner_and_named_partner_receive_same_read_only_fields(self):
        before = self.path.read_bytes()
        fish = json.loads(await self.plugin.meme_lookup(FakeEvent(), "反向领先"))
        _, specs = team_delegation.read_delegation(
            '<fishhome_delegate>[{"kind":"meme","query":"反向领先"}]</fishhome_delegate>', ["meme"])
        request = team_delegation.prepare(specs, actor="雪枝", request_id="demo", receipt_count=1)
        public = AsyncMock()
        self.assertTrue(await team_delegation.poll(request, public, lambda: True, lookup=self.plugin.look_up_meme))
        partner = request["children"][0]["lookup"]
        self.assertEqual(fish, partner)
        self.assertEqual(fish["match"], "alias")
        self.assertEqual(fish["entries"][0]["origin_status"], "reported_unverified")
        self.assertNotIn("先接梗", team_delegation.brief(request))
        self.assertIn("常见于自嘲和反讽", team_delegation.brief(request))
        self.assertEqual(team_delegation.references([request]), [])
        public.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    async def test_match_ambiguity_and_no_result(self):
        for query, match in [("倒着领先", "title"), ("DZLX", "alias"), ("倒着领", "prefix"),
                             ("你知道倒着领先吗", "embedded_title"), ("领先奖", "substring")]:
            with self.subTest(query=query):
                answer = await self.plugin.look_up_meme(query)
                self.assertEqual(answer["match"], match)
        ambiguous = await self.plugin.look_up_meme("同名")
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual({row["term"] for row in ambiguous["entries"]}, {"甲版本", "乙版本"})
        self.assertEqual((await self.plugin.look_up_meme("%"))["status"], "not_found")
        self.path.unlink()
        self.assertEqual((await self.plugin.look_up_meme("倒着领先"))["status"], "unavailable")

    async def test_tool_scope_and_turn_control(self):
        other = FakeEvent(scope=OTHER_SCOPE)
        with self.assertRaises(PermissionError):
            await self.plugin.meme_lookup(other, "倒着领先")
        with self.assertRaises(ValueError):
            await self.plugin.meme_lookup(FakeEvent(), "\n ")
        request = team_delegation.prepare([{"kind": "meme", "query": "倒着领先"}],
                                          actor="雪枝", request_id="demo", receipt_count=1)
        lookup = AsyncMock()
        self.assertFalse(await team_delegation.poll(request, None, lambda: False, lookup=lookup))
        self.assertTrue(await team_delegation.poll(request, None, lambda: False, lookup=lookup, cancel=True))
        lookup.assert_not_called()
        self.assertEqual(request["children"][0]["state"], "cancelled")
        self.assertTrue(team_delegation.available(request, types.SimpleNamespace(lookup_meme=lookup)))

    async def test_import_validates_before_replacing_the_current_dictionary(self):
        source = self.path.with_name("original.sqlite3")
        source.write_bytes(self.path.read_bytes())
        self.assertEqual(memewiki.install(source, self.path.parent), 4)
        previous = self.path.read_bytes()
        source.write_bytes(b"invalid sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            memewiki.install(source, self.path.parent)
        self.assertEqual(self.path.read_bytes(), previous)

    async def test_cnmeme_is_opt_in_and_keeps_before_and_after_sources_distinct(self):
        candidate = {"title": "倒着领先", "slug": "reverse", "summary": "公开站摘要", "tags": ["语境"]}
        details = {"item": {"content_html": "<h2>来源</h2><p>一段旧视频</p><h2>用法</h2><p>当玩笑说时是反讽。</p>"}}
        with patch.object(memewiki, "get_json", side_effect=[{"items": [candidate]}, details]) as get:
            await self.plugin.meme_lookup(FakeEvent(), "倒着领先")
            get.assert_not_called()
            answer = json.loads(await self.plugin.meme_lookup(FakeEvent(), "倒着领先", source="cnmeme"))
            self.assertEqual(get.call_count, 2)
        self.assertEqual(answer["source"], "cnmeme.wiki")
        self.assertEqual(answer["entries"][0]["origin"], "一段旧视频")
        self.assertEqual(answer["entries"][0]["usage"], "当玩笑说时是反讽。")
        with self.assertRaises(ValueError):
            team_delegation.read_delegation(
                '<fishhome_delegate>[{"kind":"meme","query":"倒着领先","source":"http"}]</fishhome_delegate>', ["meme"])
