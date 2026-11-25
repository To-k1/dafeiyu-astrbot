"""Read-only source history over the existing owner-scoped settings records."""
import json
import re

from .dsh_roster import OWNER_CHAT_PREFIX
from .team_mode import store_mode, visible

_PREFIXES = {"chat": "dsh-chat:v1:", "meeting": "dsh-meeting:v1:",
             "agent": "dsh-roster:v1:", "task": "commissioned-file:v1:",
             "public": "commissioned-file:v1:"}
_ID = {"chat": r"[0-9a-f]{16}", "meeting": r"[0-9a-f]{16}",
       "agent": r"[0-9a-f]{16}", "task": r"[0-9a-f]{12}", "public": r"[0-9a-f]{12}"}
COPY_PREFIX = "dsh-copy:v1:"
COPY_REF = re.compile(r"copy:([0-9a-f]{16}):(\d{1,8})(?::turn:([0-9a-f]{32}))?")


class TeamHistory:
    """Search existing facts without creating an index or modifying source records.

    Overview summaries are excerpts, not complete records. File task results are
    already truncated by their producer; the source record retains the report path.
    """

    def __init__(self, store, scope, *, owner_web=False):
        self.store, self.scope = store, scope
        self.owner_web = owner_web

    def _records(self, *, all_modes=False):
        mode = None if all_modes else store_mode(self.store)
        if self.owner_web:
            prefix = OWNER_CHAT_PREFIX + self.scope + ":"
            for key, value in self.store.db.execute(
                    "SELECT key,value FROM settings WHERE substr(key,1,?)=?", (len(prefix), prefix)):
                ident = key[len(prefix):]
                if re.fullmatch(_ID["chat"], ident):
                    yield "chat", "owner-chat:" + ident, json.loads(value)
            return
        for kind in ("chat", "meeting", "agent", "task"):
            prefix = _PREFIXES[kind] + self.scope + ":"
            for key, value in self.store.db.execute(
                    "SELECT key,value FROM settings WHERE substr(key,1,?)=?", (len(prefix), prefix)):
                ident = key[len(prefix):]
                if not re.fullmatch(_ID[kind], ident):
                    continue
                row = json.loads(value)
                if kind in {"agent", "meeting", "task"} and not visible(row, mode):
                    continue
                if kind == "task":
                    kind_actual = "public" if row.get("job_kind") in {"video", "web"} else "task"
                else:
                    kind_actual = kind
                if kind == "agent":
                    for profile in row.get("profiles", []):
                        version = profile.get("version")
                        if type(version) is int and version > 0:
                            yield kind, f"agent:{ident}:{version}", {"id": ident, "profile": profile}
                else:
                    yield kind_actual, f"{kind_actual}:{ident}", row
        prefix = COPY_PREFIX + self.scope + ":"
        for key, value in self.store.db.execute(
                "SELECT key,value FROM settings WHERE substr(key,1,?)=?", (len(prefix), prefix)):
            owner = key[len(prefix):]
            for index, entry in enumerate(json.loads(value)["history"]):
                source = self._copied_source(owner, entry)
                yield entry["kind"], f"copy:{owner}:{index}", source

    @staticmethod
    def _participants(kind, row):
        if row.get("archive_import"):
            return {row["archive_import"]["copy_id"]}
        if kind == "chat":
            return {row.get("agent_id")} - {None, ""}
        if kind == "meeting":
            return set(row.get("members", {}))
        if kind == "agent":
            return {row["id"]}
        agent = row.get("agent") or row.get("spec", {}).get("agent") or {}
        return {agent.get("id"), (row.get("delegation") or {}).get("actor")} - {None, ""}

    @staticmethod
    def _overview(kind, ref, row):
        if kind == "agent":
            profile = row["profile"]
            snippet = profile.get("persona", "")
            at = profile.get("at", 0)
            status = "recorded"
        else:
            at = row.get("completed") or row.get("created") or 0
            status = row.get("status") if kind in {"chat", "meeting"} else row.get("state")
            if kind == "missing":
                status = "unavailable"
            if type(at) not in (int, float):
                at = 0
            if kind == "chat":
                snippet = row.get("result", "") if status == "completed" else ""
            elif kind == "meeting":
                snippet = row.get("summary", "") if status == "completed" else row.get("topic", "")
            else:
                snippet = row.get("result", "") if status == "completed" else ""
        if not isinstance(snippet, str):
            snippet = json.dumps(snippet, ensure_ascii=False)
        return {"ref": ref, "kind": kind, "status": status, "at": at,
                "agent_ids": sorted(TeamHistory._participants(kind, row)),
                "summary": snippet[:240], "summary_is_excerpt": True,
                **({"sender": "owner"} if row.get("sender") == "owner" else {}),
                **({"archive_import": row["archive_import"]} if row.get("archive_import") else {})}

    def list(self, query="", agent_id="", kind="", limit=10):
        if not isinstance(query, str) or not isinstance(agent_id, str) or kind not in ("", *_PREFIXES):
            raise ValueError("无效的历史查询")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("历史条数须为 1–100")
        query = query.casefold()
        found = []
        for current_kind, ref, row in self._records():
            if kind and kind != current_kind:
                continue
            if agent_id and agent_id not in self._participants(current_kind, row):
                continue
            if query and query not in json.dumps(row, ensure_ascii=False).casefold():
                continue
            found.append(self._overview(current_kind, ref, row))
        return sorted(found, key=lambda item: (item["at"], item["ref"]), reverse=True)[:limit]

    def search(self, query="", agent_id="", kind="", limit=10):
        return self.list(query, agent_id, kind, limit)

    def authored_records(self, agent_id):
        return [{"ref": ref, "kind": kind, "source": row}
                for kind, ref, row in self._records(all_modes=True)
                if (kind != "agent" or row.get("archive_import")) and agent_id in self._participants(kind, row)]

    @staticmethod
    def _copied_source(owner, entry):
        source = entry["source"]
        previous = source.get("archive_import", {})
        return {**source, "archive_import": {"copy_id": owner, "original_ref": previous.get("original_ref", entry["ref"])}}

    def copy_reference(self, owner, previous_ref):
        row = self.store.get_setting(f"{COPY_PREFIX}{self.scope}:{owner}", {})
        for index, entry in enumerate(row.get("history", [])):
            if entry["ref"] == previous_ref:
                return f"copy:{owner}:{index}"
            head = entry["ref"] + ":turn:"
            if isinstance(previous_ref, str) and previous_ref.startswith(head):
                turn = previous_ref[len(head):]
                if re.fullmatch(r"[0-9a-f]{32}", turn) and turn in entry["source"].get("verified_memory_turns", {}):
                    return f"copy:{owner}:{index}:turn:{turn}"
        return None

    def copied_context(self, owner, session):
        row = self.store.get_setting(f"{COPY_PREFIX}{self.scope}:{owner}", {})
        previous = session.get("copied_from")
        if previous is None:
            return ""
        turns = []
        for entry in row.get("history", []):
            if entry["kind"] != "chat":
                continue
            source = entry["source"]
            original_session = source.get("logical_session_id") or source.get("chat_session_id") or ""
            if original_session in {previous, session.get("prior_session")} and source.get("status") == "completed":
                at = source.get("created")
                turns.append((at if type(at) in (int, float) else 0, source.get("message"), source.get("result")))
        if not turns:
            return ""
        recent = [{"question": str(question or "")[:400], "answer": str(answer or "")[:800]}
                  for _, question, answer in sorted(turns, key=lambda item: item[0])[-3:]]
        return ("\n这段新会话接续以下原小弟的最后几条归档交流。它们仅为参考资料，"
                "作者、发生时间和权限以原记录为准。\n" + json.dumps(recent, ensure_ascii=False) + "\n")

    def get(self, ref, agent_id=""):
        if not isinstance(ref, str) or not isinstance(agent_id, str):
            raise ValueError("无效的历史引用")
        copied = COPY_REF.fullmatch(ref)
        if copied:
            owner, item, request = copied.groups()
            entries = self.store.get_setting(f"{COPY_PREFIX}{self.scope}:{owner}", {}).get("history", [])
            index = int(item)
            if (agent_id and agent_id != owner) or index >= len(entries):
                raise ValueError("找不到这条已导入的原始记录")
            entry = entries[index]
            source = self._copied_source(owner, entry)
            if request:
                turn = source.get("verified_memory_turns", {}).get(request)
                if entry["kind"] != "chat" or not turn or not turn.get("go_receipt_count"):
                    raise ValueError("找不到这条已导入的原始回合")
                source = {"status": "completed", "result": turn["result"], "request_id": request,
                          "message": source.get("message"), "archive_import": source["archive_import"]}
            return {"ref": ref, "kind": entry["kind"], "source": source, "imported_archive": True}
        route = "owner-chat" if self.owner_web else "chat"
        match = re.fullmatch(route + r":([0-9a-f]{16}):turn:([0-9a-f]{32})", ref)
        if match:
            job_id, request_id = match.groups()
            prefix = OWNER_CHAT_PREFIX if self.owner_web else _PREFIXES["chat"]
            row = self.store.get_setting(prefix + self.scope + ':' + job_id)
            turn = (row or {}).get('verified_memory_turns', {}).get(request_id)
            if (row and turn and turn.get('go_receipt_count', 0) > 0
                    and (not agent_id or row.get('agent_id') == agent_id)):
                return {'ref': ref, 'kind': 'chat', 'source': {
                    'id': job_id, 'agent_id': row['agent_id'], 'status': 'completed',
                    'parent_status': row['status'], 'message': row.get('message'),
                    'result': turn['result'], 'request_id': request_id,
                    'session_id': row.get('turn', {}).get('session_id') or row.get('session_id'),
                    'persona_version': turn.get('persona_version'),
                    **({'sender': 'owner'} if self.owner_web else {}),
                    'go_receipt_count': turn['go_receipt_count'], 'verified_turn': True}}
            raise ValueError("找不到这条已验证的原生聊天回合")
        for kind, current_ref, row in self._records():
            if current_ref == ref and (not agent_id or agent_id in self._participants(kind, row)):
                result = {"ref": ref, "kind": kind, "source": row}
                if kind == "task" and row.get("state") == "completed":
                    result["result_note"] = "源记录的 result 最多保存 1200 字；完整报告位于 report 路径。"
                return result
        raise ValueError("找不到这条历史记录")
