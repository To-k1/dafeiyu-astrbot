import asyncio
import importlib.util
import pathlib
import unittest
import sys
from contextlib import contextmanager

path = pathlib.Path(__file__).resolve().parents[1] / "plugin/astrbot_plugin_bili_activity/team_memory_backend.py"
spec = importlib.util.spec_from_file_location("team_memory_backend", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
scope_path = pathlib.Path(__file__).resolve().parents[2] / "dev/astrbot-4.29.4/data/plugins/astrbot_plugin_livingmemory/core/memory_scope.py"
scope_spec = importlib.util.spec_from_file_location("test_lm_scope", scope_path)
scope = importlib.util.module_from_spec(scope_spec)
sys.modules[scope_spec.name] = scope
scope_spec.loader.exec_module(scope)

@contextmanager
def boundary(adapter_id):
    token = scope.current_adapter_access.set(scope.AdapterAccess(adapter_id, (adapter_id,)))
    try: yield
    finally: scope.current_adapter_access.reset(token)


class Store:
    def __init__(self): self.data = {}
    def get_setting(self, key): return self.data.get(key)
    def setting(self, key, value): self.data[key] = value.copy()


class Engine:
    def __init__(self): self.docs = {}; self.next_id = 1; self.fail = False
    async def add_memory(self, **kwargs):
        assert scope.current_adapter_access.get().adapter_id == kwargs["metadata"]["adapter_id"]
        ident = self.next_id; self.next_id += 1
        self.docs[ident] = {"id": ident, "text": kwargs["content"], "metadata": {
            **kwargs["metadata"], "session_id": kwargs["session_id"], "persona_id": kwargs["persona_id"],
            "importance": kwargs["importance"]}}
        if self.fail: raise RuntimeError("response lost after write")
        return ident
    async def get_memory(self, ident): return self.docs.get(ident)
    async def search_memories(self, query, k, session_id, persona_id):
        # Deliberately ignore native scope to check the adapter's second boundary.
        return list(self.docs.values())
    async def list_memories_page(self, **kwargs):
        return {"items": list(self.docs.values()), "has_more": False}
    async def replace_memory(self, ident, *, content, metadata, importance):
        new = self.next_id; self.next_id += 1
        self.docs[new] = {"id": new, "text": content, "metadata": metadata.copy()}
        del self.docs[ident]
        return new
    async def delete_memory(self, ident): return self.docs.pop(ident, None) is not None


class TeamMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store, self.engine = Store(), Engine()
        self.backend = module.TeamMemoryBackend(self.store, engine=self.engine, adapter_boundary=boundary)

    async def test_scope_subjects_sources_and_dedup(self):
        b = self.backend
        a = await b.save(owner="agent-a", source_ref={"chat": "1"}, content="A说我喜欢茶",
                         actors=["agent-a"], speakers=["agent-a"])
        other = await b.save(owner="agent-b", source_ref={"chat": "1"}, content="B喜欢茶",
                             actors=["agent-b"], speakers=["agent-b"])
        fish = await b.save(owner="fish", source_ref={"chat": "1"}, content="A说他喜欢茶；鱼很高兴",
                            actors=["agent-a", "fish"], speakers=["agent-a"], evaluation_author="fish",
                            session_id="qq:private:owner", persona_id="fish-persona", adapter_id="qq")
        self.assertEqual(a, await b.save(owner="agent-a", source_ref={"chat": "1"}, content="A说我喜欢茶",
                                         actors=["agent-a"], speakers=["agent-a"]))
        self.assertEqual([a], [x["id"] for x in await b.search(owner="agent-a", query="茶")])
        self.assertIsNone(await b.get(owner="agent-b", memory_id=a))
        m = await b.get(owner="fish", memory_id=fish, session_id="qq:private:owner", persona_id="fish-persona", adapter_id="qq")
        self.assertEqual(m["metadata"]["fishhome_speakers"], ["agent-a"])
        self.assertEqual(m["metadata"]["fishhome_evaluation_author"], "fish")
        self.assertEqual(m["metadata"]["fishhome_source_ref"], {"chat": "1"})
        self.assertNotEqual(a, other)
        with self.assertRaises(module.MemoryAlreadyExistsError):
            await b.save(owner="agent-a", source_ref={"chat": "1"}, content="different", actors=[], speakers=[])

    async def test_update_and_forget_follow_new_id(self):
        b = self.backend
        ident = await b.save(owner="a", source_ref="chat:1", content="旧事实", actors=["a"], speakers=["a"])
        new = await b.update(owner="a", source_ref="chat:1", content="修正事实")
        self.assertNotEqual(ident, new)
        self.assertEqual((await b.get(owner="a", memory_id=new))["text"], "修正事实")
        self.assertTrue(await b.forget(owner="a", source_ref="chat:1"))
        self.assertIsNone(await b.get(owner="a", memory_id=new))
        with self.assertRaises(module.MemoryUncertainError):
            await b.save(owner="a", source_ref="chat:1", content="重复", actors=[], speakers=[])

    async def test_uncertain_write_does_not_retry(self):
        self.engine.fail = True
        with self.assertRaises(RuntimeError):
            await self.backend.save(owner="a", source_ref="1", content="事实", actors=["a"], speakers=["a"])
        self.engine.fail = False
        with self.assertRaises(module.MemoryUncertainError):
            await self.backend.save(owner="a", source_ref="1", content="事实", actors=["a"], speakers=["a"])
        self.assertEqual(len(self.engine.docs), 1)

    async def test_fish_requires_existing_scope(self):
        with self.assertRaises(ValueError):
            await self.backend.search(owner="fish", query="x")

    async def test_real_lm_adapter_boundary_override_and_restore(self):
        with boundary("qq"):
            await self.backend.save(owner="agent-a", source_ref="a", content="经历", actors=["agent-a"], speakers=["agent-a"])
            self.assertEqual(scope.current_adapter_access.get().adapter_id, "qq")
            self.assertFalse(scope.adapter_visible({"adapter_id": "fishhome-dsh:agent-a"}))
        with boundary("fishhome-dsh:agent-a"):
            self.assertTrue(scope.adapter_visible({"adapter_id": "fishhome-dsh:agent-a"}))
            self.assertFalse(scope.adapter_visible({"adapter_id": "fishhome-dsh:agent-b"}))
            self.assertEqual(scope.adapter_sql("metadata")[1], ["fishhome-dsh:agent-a"])
        self.assertIsNone(scope.current_adapter_access.get())

    async def test_reconcile_uncertain_save_without_blind_retry(self):
        self.engine.fail = True
        with self.assertRaises(RuntimeError):
            await self.backend.save(owner="a", source_ref="1", content="fact", actors=[], speakers=[])
        self.engine.fail = False
        result = await self.backend.reconcile(owner="a", source_ref="1")
        self.assertEqual(result, {"status": "recovered", "memory_ids": [1]})
        self.assertEqual(await self.backend.save(owner="a", source_ref="1", content="fact", actors=[], speakers=[]), 1)

    async def test_reconcile_zero_and_multiple_require_review(self):
        key = self.backend._key("a", "x", *self.backend.agent_scope("a"), "fishhome-dsh:a")
        self.store.setting(key, {"status": "uncertain"})
        self.assertEqual((await self.backend.reconcile(owner="a", source_ref="x"))["status"], "review")
        # Simulate two writes with a lost receipt, using the same native metadata.
        namespace = self.backend.agent_scope("a")[0]
        for i in (1, 2):
            self.engine.docs[i] = {"id": i, "text": "x", "metadata": {"fishhome_owner": "a", "fishhome_source_ref": "x", "session_id": namespace, "persona_id": namespace, "adapter_id": "fishhome-dsh:a"}}
        self.assertEqual((await self.backend.reconcile(owner="a", source_ref="x"))["memory_ids"], [1, 2])
        self.assertEqual(self.store.get_setting(key)["status"], "uncertain")
