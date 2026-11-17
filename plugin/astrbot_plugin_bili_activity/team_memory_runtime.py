"""Own the isolated DSH LivingMemory index, reusing the loaded LM implementation."""
import asyncio
import importlib
from pathlib import Path

from .team_memory_backend import TeamMemoryBackend


class TeamMemoryRuntime:
    def __init__(self, store, context, data_dir, *, adapter_boundary=None):
        self.store, self.context = store, context
        self.data_dir = Path(data_dir) / "dsh_team_memory"
        self.adapter_boundary = adapter_boundary
        self._lock = asyncio.Lock()
        self._fish = self._team = self._engine = self._db = None
        self._source_engine = self._source_provider = None

    def _plugin(self):
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        plugin = getattr(metadata, "star_cls", None)
        if not metadata or not getattr(metadata, "activated", False) or not plugin:
            raise RuntimeError("LivingMemory plugin is not active")
        init = getattr(plugin, "initializer", None)
        if not init or not getattr(init, "memory_engine", None) or not getattr(init, "embedding_provider", None):
            raise RuntimeError("LivingMemory engine or embedding provider is not ready")
        return plugin

    async def backend(self, owner):
        if not isinstance(owner, str) or not owner:
            raise ValueError("owner is required")
        async with self._lock:
            plugin = self._plugin()
            init = plugin.initializer
            if self._source_engine is not init.memory_engine or self._source_provider is not init.embedding_provider:
                engine, db = self._engine, self._db
                self._fish = self._team = self._engine = self._db = None
                self._source_engine, self._source_provider = init.memory_engine, init.embedding_provider
                try:
                    if engine is not None:
                        await engine.close()
                finally:
                    if db is not None:
                        await db.close()
            if owner == "fish":
                if self._fish is None:
                    self._fish = TeamMemoryBackend(self.store, lmplugin=plugin, adapter_boundary=self.adapter_boundary)
                return self._fish
            if self._team is not None:
                return self._team
            init = plugin.initializer
            original = init.memory_engine
            db_path = self.data_dir / "livingmemory.db"
            index_path = self.data_dir / "livingmemory.index"
            if db_path.resolve() == Path(original.db_path).resolve():
                raise ValueError("DSH memory directory overlaps the fish database")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            db = type(init.db)(str(db_path), str(index_path), init.embedding_provider)
            engine = None
            try:
                await db.initialize()
                engine = type(original)(db_path=str(db_path), faiss_db=db,
                                        config={"graph_memory_enabled": False, "atom_enabled": False})
                await engine.initialize()
                team = TeamMemoryBackend(self.store, engine=engine, adapter_boundary=self.adapter_boundary)
            except BaseException:
                if engine is not None:
                    await engine.close()
                await db.close()
                raise
            self._db, self._engine, self._team = db, engine, team
            return team

    async def fish_scope(self, event):
        plugin = self._plugin()
        engine_module = type(plugin.initializer.memory_engine).__module__
        prefix = engine_module.split(".core.", 1)[0]
        scope = importlib.import_module(prefix + ".core.memory_scope")
        utils = importlib.import_module(prefix + ".core.utils")
        config = plugin.initializer.config_manager
        if not scope.is_event_memory_allowed(config, event):
            raise PermissionError("LivingMemory access is not allowed for this event")
        adapter_id = event.get_platform_id() or event.unified_msg_origin.partition(":")[0]
        if not adapter_id:
            raise ValueError("event has no adapter ID")
        return {"session_id": scope.resolve_memory_scope(config, event) or event.unified_msg_origin,
                "persona_id": await utils.get_persona_id(self.context, event), "adapter_id": adapter_id}

    async def close(self):
        async with self._lock:
            engine, db = self._engine, self._db
            self._engine = self._db = self._team = self._fish = None
            self._source_engine = self._source_provider = None
            try:
                if engine is not None:
                    await engine.close()
            finally:
                if db is not None:
                    await db.close()
