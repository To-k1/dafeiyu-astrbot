"""Local native LM integration; deterministic embeddings, no network or models."""
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT.parent / "dev/astrbot-4.29.4/data/plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
    from astrbot.core.provider.provider import EmbeddingProvider
    from astrbot_plugin_livingmemory.core.managers.memory_engine import MemoryEngine
    from astrbot_plugin_livingmemory.core.memory_scope import adapter_access
    IMPORT_ERROR = None
except (ImportError, OSError) as exc:
    IMPORT_ERROR = str(exc)

package = types.ModuleType("team_native_test_package")
package.__path__ = [str(ROOT / "plugin/astrbot_plugin_bili_activity")]
sys.modules[package.__name__] = package
for name in ("team_memory_backend", "team_memory_runtime"):
    spec = importlib.util.spec_from_file_location(package.__name__ + "." + name,
                                                   pathlib.Path(package.__path__[0]) / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if name == "team_memory_runtime": Runtime = mod.TeamMemoryRuntime


if IMPORT_ERROR is None:
    class Embedding(EmbeddingProvider):
        def __init__(self): super().__init__({"id": "test", "type": "test"}, {})
        def get_dim(self): return 24
        async def get_embedding(self, text):
            result = [0.0] * self.get_dim()
            for i, value in enumerate(text.encode("utf-8")):
                result[i % len(result)] += (value % 31 + 1) / 31
            norm = sum(x*x for x in result) ** 0.5 or 1
            return [x / norm for x in result]
        async def get_embeddings(self, texts):
            return [await self.get_embedding(text) for text in texts]


class Settings:
    def __init__(self): self.data = {}
    def get_setting(self, key): return self.data.get(key)
    def setting(self, key, value): self.data[key] = dict(value)


@unittest.skipIf(IMPORT_ERROR is not None, "native LivingMemory dependency unavailable: " + str(IMPORT_ERROR))
class NativeMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_crud_isolation_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp)
            provider = Embedding()
            fishdb = FaissVecDB(str(path / "fish.db"), str(path / "fish.index"), provider)
            await fishdb.initialize()
            fishengine = MemoryEngine(str(path / "fish.db"), fishdb,
                                      config={"graph_memory_enabled": False, "atom_enabled": False})
            await fishengine.initialize()
            init = types.SimpleNamespace(memory_engine=fishengine, db=fishdb, embedding_provider=provider)
            plugin = types.SimpleNamespace(initializer=init)
            context = types.SimpleNamespace(get_registered_star=lambda _: types.SimpleNamespace(activated=True, star_cls=plugin))
            store = Settings()
            runtime = Runtime(store, context, path / "activity")
            try:
                fish = await runtime.backend("fish")
                bro = await runtime.backend("bro-a")
                fish_scope = {"session_id": "qq:FriendMessage:owner", "persona_id": "fish", "adapter_id": "qq"}
                fishid = await fish.save(owner="fish", source_ref="fish-chat", content="fish remembers apple",
                    actors=["fish", "bro-a"], speakers=["bro-a"], evaluation_author="fish", **fish_scope)
                aid = await bro.save(owner="bro-a", source_ref={"chat": "1"}, content="bro-a likes tea",
                    actors=["bro-a"], speakers=["bro-a"])
                bid = await bro.save(owner="bro-b", source_ref={"chat": "1"}, content="bro-b likes tea",
                    actors=["bro-b"], speakers=["bro-b"])
                namespace = bro.agent_scope("bro-a")
                receipt_key = bro._key("bro-a", {"chat": "1"}, *namespace, "fishhome-dsh:bro-a")
                store.setting(receipt_key, {"status": "uncertain", "owner": "bro-a", "source_ref": {"chat": "1"}})
                self.assertEqual((await bro.reconcile(owner="bro-a", source_ref={"chat": "1"}))["memory_ids"], [aid])
                hits = await bro.search(owner="bro-a", query="likes tea", k=10)
                self.assertIn(aid, [x.doc_id for x in hits])
                self.assertNotIn(bid, [x.doc_id for x in hits])
                self.assertIsNone(await bro.get(owner="bro-b", memory_id=aid))
                mem = await fish.get(owner="fish", memory_id=fishid, **fish_scope)
                self.assertEqual(mem["metadata"]["fishhome_speakers"], ["bro-a"])
                self.assertEqual(mem["metadata"]["fishhome_evaluation_author"], "fish")
                self.assertEqual(mem["metadata"]["fishhome_source_ref"], "fish-chat")
                self.assertIn(fishid, [x.doc_id for x in await fish.search(owner="fish", query="apple", k=10, **fish_scope)])
                with adapter_access({"filtering_settings": {"shared_adapter_ids": []}}, "fishhome-dsh:bro-a"):
                    native_hits = await bro.engine.search_memories("likes tea", 10)
                    self.assertIn(aid, [x.doc_id for x in native_hits])
                    self.assertNotIn(bid, [x.doc_id for x in native_hits])
                with adapter_access({}, "qq"):
                    normal = await fishengine.search_memories("likes tea", 10)
                self.assertFalse(any("bro-a likes tea" in x.content or "bro-b likes tea" in x.content for x in normal))
                revised = await bro.update(owner="bro-a", source_ref={"chat": "1"}, content="bro-a likes coffee")
                self.assertNotEqual(aid, revised)
                revised_record = await bro.get(owner="bro-a", memory_id=revised)
                self.assertEqual(revised_record["metadata"]["fishhome_source_ref"], {"chat": "1"})
                await runtime.close()
                reopened = await runtime.backend("bro-a")
                self.assertEqual((await reopened.get(owner="bro-a", memory_id=revised))["text"], "bro-a likes coffee")
                self.assertIn(revised, [x.doc_id for x in await reopened.search(owner="bro-a", query="coffee", k=10)])
                self.assertTrue(await reopened.forget(owner="bro-a", source_ref={"chat": "1"}))
                self.assertIsNone(await reopened.get(owner="bro-a", memory_id=revised))
                self.assertIsNotNone(await reopened.get(owner="bro-b", memory_id=bid))
                await fishengine.close()
                await fishdb.close()
                fishdb = FaissVecDB(str(path / "fish.db"), str(path / "fish.index"), provider)
                await fishdb.initialize()
                fishengine = MemoryEngine(str(path / "fish.db"), fishdb,
                                          config={"graph_memory_enabled": False, "atom_enabled": False})
                await fishengine.initialize()
                with adapter_access({}, "qq"):
                    self.assertEqual((await fishengine.get_memory(fishid))["text"], "fish remembers apple")
            finally:
                await runtime.close()
                await fishengine.close()
                await fishdb.close()
