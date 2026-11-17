"""Thin, ownership-aware access to the existing LivingMemory engine.

Only reference receipts live in the activity settings store; memory text remains
in LivingMemory.  An uncertain receipt requires reconciliation by the caller.
"""
import asyncio
import hashlib
import json
import importlib
from contextlib import contextmanager


class MemoryUncertainError(RuntimeError):
    """A previous write may have reached LivingMemory; do not retry blindly."""


class MemoryAlreadyExistsError(ValueError):
    """The reference already identifies a different memory; use update."""


class TeamMemoryBackend:
    def __init__(self, store, *, engine=None, lmplugin=None, adapter_boundary=None):
        self.store = store
        self.engine = engine or getattr(getattr(lmplugin, "initializer", None), "memory_engine", None)
        if self.engine is None:
            raise ValueError("LivingMemory engine is unavailable")
        if adapter_boundary is None:
            module_name = type(self.engine).__module__.split(".core.", 1)[0]
            if module_name == type(self.engine).__module__:
                raise ValueError("adapter_boundary is required for this engine")
            scope_module = importlib.import_module(module_name + ".core.memory_scope")
            @contextmanager
            def adapter_boundary(adapter_id):
                token = scope_module.current_adapter_access.set(
                    scope_module.AdapterAccess(adapter_id, (adapter_id,)))
                try:
                    yield
                finally:
                    scope_module.current_adapter_access.reset(token)
        self._boundary = adapter_boundary
        self._lock = asyncio.Lock()

    @staticmethod
    def agent_scope(agent_id):
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id is required")
        # Encode the ID to avoid collisions with delimiters and other namespaces.
        digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()
        namespace = "livingmemory:fishhome:agent:" + digest
        return namespace, namespace

    @staticmethod
    def _adapter(owner, adapter_id):
        if owner == "fish":
            if not adapter_id:
                raise ValueError("fish requires the actual adapter_id")
            return adapter_id
        return "fishhome-dsh:" + owner

    @staticmethod
    def _key(owner, source_ref, session_id, persona_id, adapter_id):
        raw = json.dumps([owner, source_ref, session_id, persona_id, adapter_id], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "team_memory_ref:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _scope(owner, session_id, persona_id):
        if not owner or not isinstance(owner, str):
            raise ValueError("owner is required")
        if owner == "fish":
            return session_id, persona_id
        return TeamMemoryBackend.agent_scope(owner)

    @staticmethod
    def _metadata(memory):
        metadata = memory.get("metadata", {}) if isinstance(memory, dict) else getattr(memory, "metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return metadata if isinstance(metadata, dict) else {}

    @classmethod
    def _owned(cls, memory, owner, session_id, persona_id, adapter_id):
        meta = cls._metadata(memory)
        return (meta.get("fishhome_owner") == owner and meta.get("session_id") == session_id
                and meta.get("persona_id") == persona_id and meta.get("adapter_id") == adapter_id)

    async def save(self, *, owner, source_ref, content, actors, speakers, evaluation_author=None,
                   session_id=None, persona_id=None, importance=0.5, adapter_id=None, summary_author=None):
        if not source_ref or not content or not content.strip():
            raise ValueError("source_ref and content are required")
        if not isinstance(actors, list) or not isinstance(speakers, list):
            raise ValueError("actors and speakers must be explicit lists")
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        key = self._key(owner, source_ref, session_id, persona_id, adapter_id)
        async with self._lock:
            receipt = self.store.get_setting(key)
            if receipt:
                if receipt.get("status") == "saved":
                    with self._boundary(adapter_id):
                        existing = await self.engine.get_memory(receipt["memory_id"])
                    if existing is None or not self._owned(existing, owner, session_id, persona_id, adapter_id):
                        raise MemoryUncertainError("saved memory cannot be verified")
                    if existing.get("text") != content:
                        raise MemoryAlreadyExistsError("reference already exists with different content; use update")
                    return receipt["memory_id"]
                raise MemoryUncertainError("reference requires reconciliation: " + str(source_ref))
            self.store.setting(key, {"status": "uncertain", "owner": owner, "source_ref": source_ref})
            metadata = {"fishhome_owner": owner, "fishhome_actors": actors,
                        "fishhome_speakers": speakers, "fishhome_evaluation_author": evaluation_author,
                        "fishhome_summary_author": summary_author,
                        "fishhome_source_ref": source_ref, "adapter_id": adapter_id}
            with self._boundary(adapter_id):
                memory_id = await self.engine.add_memory(content=content, session_id=session_id,
                    persona_id=persona_id, importance=importance, metadata=metadata)
            if not isinstance(memory_id, int) or isinstance(memory_id, bool):
                raise MemoryUncertainError("LivingMemory did not confirm an ID")
            self.store.setting(key, {"status": "saved", "owner": owner,
                                     "source_ref": source_ref, "memory_id": memory_id})
            return memory_id

    async def get(self, *, owner, memory_id, session_id=None, persona_id=None, adapter_id=None):
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        with self._boundary(adapter_id):
            memory = await self.engine.get_memory(memory_id)
        return memory if memory and self._owned(memory, owner, session_id, persona_id, adapter_id) else None

    async def search(self, *, owner, query, k=5, session_id=None, persona_id=None, adapter_id=None):
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        with self._boundary(adapter_id):
            results = await self.engine.search_memories(query, k=k, session_id=session_id, persona_id=persona_id)
        return [item for item in results if self._owned(item, owner, session_id, persona_id, adapter_id)]

    async def update(self, *, owner, source_ref, content, session_id=None, persona_id=None, importance=None, adapter_id=None):
        if not content or not content.strip():
            raise ValueError("content is required")
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        key = self._key(owner, source_ref, session_id, persona_id, adapter_id)
        async with self._lock:
            receipt = self.store.get_setting(key)
            if not receipt or receipt.get("status") != "saved":
                raise MemoryUncertainError("reference missing or requires reconciliation")
            old_id = receipt["memory_id"]
            memory = await self.get(owner=owner, memory_id=old_id, session_id=session_id, persona_id=persona_id, adapter_id=adapter_id)
            if memory is None:
                raise MemoryUncertainError("memory cannot be verified")
            self.store.setting(key, {**receipt, "status": "uncertain", "operation": "update"})
            meta = self._metadata(memory)
            with self._boundary(adapter_id):
                new_id = await self.engine.replace_memory(old_id, content=content, metadata=meta,
                    importance=meta.get("importance", 0.5) if importance is None else importance)
            if not isinstance(new_id, int) or isinstance(new_id, bool):
                raise MemoryUncertainError("replacement did not confirm an ID")
            self.store.setting(key, {**receipt, "memory_id": new_id})
            return new_id

    async def reconcile(self, *, owner, source_ref, session_id=None, persona_id=None, adapter_id=None):
        """Inspect every scoped document; repair only an unambiguous interrupted save."""
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        key = self._key(owner, source_ref, session_id, persona_id, adapter_id)
        async with self._lock:
            receipt = self.store.get_setting(key)
            if not receipt or receipt.get("status") != "uncertain":
                raise ValueError("reference does not have an uncertain receipt")
            matches, page = [], 1
            with self._boundary(adapter_id):
                while True:
                    batch = await self.engine.list_memories_page(session_id=session_id, page=page, page_size=100, sort_key="id_asc")
                    for item in batch["items"]:
                        meta = self._metadata(item)
                        if self._owned(item, owner, session_id, persona_id, adapter_id) and meta.get("fishhome_source_ref") == source_ref:
                            matches.append(item["id"])
                    if not batch["has_more"]:
                        break
                    page += 1
            operation = receipt.get("operation", "save")
            if len(matches) == 1 and operation == "save":
                self.store.setting(key, {**receipt, "status": "saved", "memory_id": matches[0]})
                return {"status": "recovered", "memory_ids": matches}
            return {"status": "review", "operation": operation, "memory_ids": matches,
                    "reason": "no matching memory" if not matches else "manual review required"}

    async def forget(self, *, owner, source_ref, session_id=None, persona_id=None, adapter_id=None):
        session_id, persona_id = self._scope(owner, session_id, persona_id)
        adapter_id = self._adapter(owner, adapter_id)
        key = self._key(owner, source_ref, session_id, persona_id, adapter_id)
        async with self._lock:
            receipt = self.store.get_setting(key)
            if not receipt or receipt.get("status") != "saved":
                raise MemoryUncertainError("reference missing or requires reconciliation")
            if await self.get(owner=owner, memory_id=receipt["memory_id"], session_id=session_id, persona_id=persona_id, adapter_id=adapter_id) is None:
                raise MemoryUncertainError("memory cannot be verified")
            self.store.setting(key, {**receipt, "status": "uncertain", "operation": "forget"})
            with self._boundary(adapter_id):
                deleted = await self.engine.delete_memory(receipt["memory_id"])
            if not deleted:
                raise MemoryUncertainError("deletion was not confirmed")
            self.store.setting(key, {**receipt, "status": "forgotten"})
            return True
