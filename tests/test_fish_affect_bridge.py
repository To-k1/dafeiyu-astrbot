"""Integration of the existing activity entry, bridge and real affective SQLite store."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_affective_core as affective
import test_bili_activity as bili
import test_bili_life_integration as life
from plugin.astrbot_plugin_dafeiyu_affect_bridge.main import BACKGROUND, SERVICE, FishAffectBridge


class BridgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    save_config = life.LifeIntegrationTests.save_config

    async def asyncSetUp(self):
        await life.LifeIntegrationTests.asyncSetUp(self)
        self.addAsyncCleanup(life.LifeIntegrationTests.asyncTearDown, self)
        self.context = SimpleNamespace()
        self.plugin.context = self.context
        self.affect_dir = Path(self.folder.name) / "affective"
        self.affect_config = {"owner_scope": bili.SCOPE, "owner_id": "12345",
                              "appraisal_mode": "math_only", "inject_prompt_memo": False}
        self.core = affective.AffectiveCorePlugin(self.context, self.affect_config, data_dir=self.affect_dir)
        self.bridge = FishAffectBridge(self.context, self.affect_config | {
            "chat_background_enabled": True, "life_events_enabled": True})
        await self.bridge.initialize()
        self.addAsyncCleanup(self.close_plugins)
        self.event = bili.FakeEvent(event_id="observed")
        self.observation = self.plugin.store.life.record_episode(
            bili.SCOPE, kind="video", source_id="observed",
            evidence={"bvid": "BVexample", "cid": "1", "subtitles": [{"content": "说明"}]})["id"]

    async def close_plugins(self):
        await self.bridge.terminate()
        await self.core.terminate()

    async def test_mixed_appraisal_retains_evidence_without_vote_or_pulse(self):
        thought = "前半段无聊，后半段装置很有意思。"
        result = json.loads(await self.plugin.interest(
            self.event, self.observation, "装置", thought=thought))
        row = self.plugin.store.life._episode(self.plugin.store.db.execute(
            "SELECT * FROM episodes WHERE id=?", (result["episode_id"],)).fetchone())
        self.assertEqual(row["evidence"]["episode_id"], self.observation)
        self.assertEqual(row["interpretation"]["thought"], thought)
        self.assertIsNone(row["interpretation"]["interest"]["polarity"])
        self.assertEqual(result["interests"], [])
        self.assertEqual(bili.main.MemoryPolicy(self.plugin.store).observed_interests(bili.SCOPE), [])
        repeat = json.loads(await self.plugin.interest(self.event, self.observation, "装置", thought=thought))
        revised = json.loads(await self.plugin.interest(
            self.event, self.observation, "装置", thought="还没决定以后会不会继续看。"))
        self.assertEqual(repeat["episode_id"], result["episode_id"])
        self.assertNotEqual(revised["episode_id"], result["episode_id"])
        self.assertIsNone(self.core.store.get_owner_state(bili.SCOPE))

    async def test_explicit_interest_pulses_once_and_survives_reopen(self):
        previous = self.plugin.store.life.record_episode(
            bili.SCOPE, kind="interest",
            source_id=bili.core.digest([self.observation, "装置", 1]),
            evidence={"episode_id": self.observation, "bvid": "BVexample", "cid": "1"},
            interpretation={"interest": {"topic": "装置", "polarity": 1}, "explicit_polarity": False})
        self.assertEqual(self.plugin.store.life.interests(bili.SCOPE), [])
        self.assertEqual(bili.main.MemoryPolicy(self.plugin.store).observed_interests(bili.SCOPE), [])
        self.assertIsNone(self.core.store.get_owner_state(bili.SCOPE))
        before = self.core.owner_snapshot(bili.SCOPE)["state"]["valence"]
        result = json.loads(await self.plugin.interest(self.event, self.observation, "装置", polarity=1, thought="有意思"))
        self.assertEqual(result["affect"], "applied")
        saved = self.plugin.store.life._episode(self.plugin.store.db.execute(
            "SELECT * FROM episodes WHERE id=?", (result["episode_id"],)).fetchone())
        self.assertEqual(saved["supersedes_id"], previous["id"])
        self.assertEqual(saved["interpretation"]["thought"], "有意思")
        self.assertIs(saved["interpretation"]["explicit_polarity"], True)
        self.assertEqual([item["polarity"] for item in bili.main.MemoryPolicy(
            self.plugin.store).observed_interests(bili.SCOPE)], [1])
        self.assertAlmostEqual(self.core.store.get_owner_state(bili.SCOPE)["valence"], before + 0.18, places=3)
        self.assertEqual(len(self.core.store.recent_events(bili.SCOPE)), 1)
        await self.core.terminate()
        self.core = affective.AffectiveCorePlugin(self.context, self.affect_config, data_dir=self.affect_dir)
        self.plugin.store.close()
        self.plugin.store = bili.core.Store(self.folder.name)
        result = json.loads(await self.plugin.interest(self.event, self.observation, "评价修订", polarity=-1))
        self.assertEqual(result["affect"], "already_forwarded")
        self.assertEqual(len(self.core.store.recent_events(bili.SCOPE)), 1)

    async def test_uncertain_delivery_is_not_replayed(self):
        core = getattr(self.context, "_dafeiyu_affective_bridge")
        apply = core.record_life_event
        def lose_receipt(*args, **kwargs):
            apply(*args, **kwargs)
            raise OSError("receipt lost")
        with patch.object(core, "record_life_event", side_effect=lose_receipt):
            first = json.loads(await self.plugin.interest(self.event, self.observation, "装置", polarity=-1))
        second = json.loads(await self.plugin.interest(self.event, self.observation, "装置", polarity=-1))
        self.assertEqual((first["affect"], second["affect"]), ("uncertain", "uncertain"))
        self.assertEqual(len(self.core.store.recent_events(bili.SCOPE)), 1)

    async def test_original_hook_and_bridge_append_one_scoped_background(self):
        await self.plugin.interest(self.event, self.observation, "装置", polarity=1)
        owner = bili.FakeEvent(event_id="chat", text="刚才试了下。")
        owner.get_platform_name = lambda: "aiocqhttp"
        owner.message_obj.raw_message = {"post_type": "message"}
        req = SimpleNamespace(system_prompt="fixture persona", contexts=[])
        await self.core.inject_affective_context(owner, req)
        await self.bridge.add_background(owner, req)
        await self.bridge.add_background(owner, req)
        self.assertEqual(req.system_prompt.count(BACKGROUND), 1)
        self.assertIn("近期对一项已记录的活动表达过兴趣", req.system_prompt)
        self.assertNotIn("【当下心境】", req.system_prompt)
        for event, platform, post_type in (
            (bili.FakeEvent(user=None), "aiocqhttp", "message"),
            (bili.FakeEvent(user="999"), "aiocqhttp", "message"),
            (bili.FakeEvent(run_id="synthetic"), "aiocqhttp", "message"),
            (bili.FakeEvent(scope="qq:GroupMessage:12345"), "aiocqhttp", "message"),
            (bili.FakeEvent(), "aiocqhttp", "notice"),
            (bili.FakeEvent(), "cron", "message"),
        ):
            event.get_platform_name = lambda: platform
            event.message_obj.raw_message = {"post_type": post_type}
            req = SimpleNamespace(system_prompt="fixture persona")
            await self.bridge.add_background(event, req)
            self.assertEqual(req.system_prompt, "fixture persona")
        await self.bridge.terminate()
        self.assertFalse(hasattr(self.context, SERVICE))
