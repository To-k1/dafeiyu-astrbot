import asyncio
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext

base = pathlib.Path(__file__).resolve().parents[1] / "plugin/astrbot_plugin_bili_activity"
pkg = types.ModuleType("team_memory_test_package")
pkg.__path__ = [str(base)]
sys.modules[pkg.__name__] = pkg
for name in ("team_memory_backend", "team_memory_runtime"):
    spec = importlib.util.spec_from_file_location(pkg.__name__ + "." + name, base / (name + ".py"))
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    if name == "team_memory_runtime": runtime_module = obj


class DB:
    created = []
    def __init__(self, db, index, provider):
        self.path, self.index, self.provider, self.closed = db, index, provider, False
        DB.created.append(self)
    async def initialize(self): pass
    async def close(self): self.closed = True


class Engine:
    def __init__(self, db_path, faiss_db, **kwargs):
        self.db_path, self.faiss_db, self.closed = db_path, faiss_db, False
    async def initialize(self): pass
    async def close(self): self.closed = True


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_separate_engine_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            provider = object()
            fishdb = DB(str(root / 'fish' / 'fish.db'), str(root / 'fish' / 'fish.index'), provider)
            fishengine = Engine(fishdb.path, fishdb)
            initializer = types.SimpleNamespace(db=fishdb, memory_engine=fishengine, embedding_provider=provider)
            plugin = types.SimpleNamespace(initializer=initializer)
            context = types.SimpleNamespace(get_registered_star=lambda name: types.SimpleNamespace(activated=True, star_cls=plugin))
            rt = runtime_module.TeamMemoryRuntime(object(), context, root / 'activity', adapter_boundary=lambda x: nullcontext())
            fish = await rt.backend('fish')
            a, b = await asyncio.gather(rt.backend('a'), rt.backend('b'))
            self.assertIs(a, b)
            self.assertIs(fish.engine, fishengine)
            self.assertIsNot(a.engine, fishengine)
            self.assertNotEqual(a.engine.db_path, fishengine.db_path)
            self.assertEqual(pathlib.Path(a.engine.db_path).parent, root / 'activity' / 'dsh_team_memory')
            self.assertIs(a.engine.faiss_db.provider, provider)
            newengine, newdb = a.engine, a.engine.faiss_db
            await rt.close()
            self.assertTrue(newengine.closed and newdb.closed)
            self.assertFalse(fishengine.closed or fishdb.closed)

    async def test_reload_replaces_cached_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            provider = object()
            db = DB(str(root / 'fish.db'), str(root / 'fish.index'), provider)
            initial = Engine(db.path, db)
            initializer = types.SimpleNamespace(db=db, memory_engine=initial, embedding_provider=provider)
            plugin = types.SimpleNamespace(initializer=initializer)
            context = types.SimpleNamespace(get_registered_star=lambda _: types.SimpleNamespace(activated=True, star_cls=plugin))
            rt = runtime_module.TeamMemoryRuntime(object(), context, root / 'activity', adapter_boundary=lambda x: nullcontext())
            old_fish, old_team = await rt.backend('fish'), await rt.backend('a')
            new_provider = object()
            new_db = DB(str(root / 'fish.db'), str(root / 'fish.index'), new_provider)
            new_fish_engine = Engine(new_db.path, new_db)
            initializer.db, initializer.memory_engine, initializer.embedding_provider = new_db, new_fish_engine, new_provider
            new_fish, new_team = await rt.backend('fish'), await rt.backend('a')
            self.assertIsNot(old_fish, new_fish)
            self.assertIs(new_fish.engine, new_fish_engine)
            self.assertTrue(old_team.engine.closed)
            self.assertTrue(old_team.engine.faiss_db.closed)
            self.assertIs(new_team.engine.faiss_db.provider, new_provider)
            await rt.close()
