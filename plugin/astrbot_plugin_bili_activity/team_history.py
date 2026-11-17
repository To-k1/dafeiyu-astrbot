"""Read-only source history over the existing owner-scoped settings records."""
import json
import re


_PREFIXES = {"chat": "dsh-chat:v1:", "meeting": "dsh-meeting:v1:",
             "agent": "dsh-roster:v1:", "task": "commissioned-file:v1:",
             "public": "commissioned-file:v1:"}
_ID = {"chat": r"[0-9a-f]{16}", "meeting": r"[0-9a-f]{16}",
       "agent": r"[0-9a-f]{16}", "task": r"[0-9a-f]{12}", "public": r"[0-9a-f]{12}"}


class TeamHistory:
    """Search existing facts without creating an index or modifying source records.

    Overview summaries are excerpts, not complete records. File task results are
    already truncated by their producer; the source record retains the report path.
    """

    def __init__(self, store, scope):
        self.store, self.scope = store, scope

    def _records(self):
        for kind in ("chat", "meeting", "agent", "task"):
            prefix = _PREFIXES[kind] + self.scope + ":"
            for key, value in self.store.db.execute(
                    "SELECT key,value FROM settings WHERE substr(key,1,?)=?", (len(prefix), prefix)):
                ident = key[len(prefix):]
                if not re.fullmatch(_ID[kind], ident):
                    continue
                row = json.loads(value)
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

    @staticmethod
    def _participants(kind, row):
        if kind == "chat":
            return {row.get("agent_id")} - {None, ""}
        if kind == "meeting":
            return set(row.get("members", {}))
        if kind == "agent":
            return {row["id"]}
        agent = row.get("agent") or row.get("spec", {}).get("agent") or {}
        return {agent.get("id")} - {None, ""}

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
                "summary": snippet[:240], "summary_is_excerpt": True}

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

    def get(self, ref, agent_id=""):
        if not isinstance(ref, str) or not isinstance(agent_id, str):
            raise ValueError("无效的历史引用")
        for kind, current_ref, row in self._records():
            if current_ref == ref and (not agent_id or agent_id in self._participants(kind, row)):
                result = {"ref": ref, "kind": kind, "source": row}
                if kind == "task" and row.get("state") == "completed":
                    result["result_note"] = "源记录的 result 最多保存 1200 字；完整报告位于 report 路径。"
                return result
        raise ValueError("找不到这条历史记录")
