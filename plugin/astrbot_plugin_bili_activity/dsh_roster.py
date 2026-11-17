"""Owner-scoped identities and their native DSH conversation references."""
import json
import re
from time import time
from uuid import uuid4

from .dsh_task import CHAT_PRESET, DshTaskFailure, GoQuotaWaiting, _open_session, _prompt, _wait_for_turn


PREFIX = "dsh-roster:v1:"
AGENT_ID = re.compile(r"[0-9a-f]{16}")


class DshRoster:
    def __init__(self, store, scope):
        self.store = store
        self.prefix = f"{PREFIX}{scope}:"

    def get(self, ident):
        if not isinstance(ident, str) or not AGENT_ID.fullmatch(ident):
            raise ValueError("请使用名册中的小弟 ID")
        row = self.store.get_setting(self.prefix + ident)
        if not row:
            raise ValueError("找不到这位小弟")
        return row

    def list(self):
        return [self.public(json.loads(row[0])) for row in self.store.db.execute(
            "SELECT value FROM settings WHERE substr(key,1,?)=? ORDER BY json_extract(value,'$.created')",
            (len(self.prefix), self.prefix))]

    @staticmethod
    def public(row):
        return {key: row.get(key) for key in ("id", "name", "status", "version", "chat_session", "created", "updated")}

    @staticmethod
    def validate_profile(name, persona):
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 60):
            raise ValueError("请给小弟填一个 1–60 字的名字")
        if not isinstance(persona, str) or not persona.strip() or len(persona) > 16000:
            raise ValueError("请提供你为这位小弟写的非空人格正文（限 16000 字）")

    def create(self, name, persona):
        self.validate_profile(name, persona)
        ident = uuid4().hex[:16]
        now = time()
        row = {"id": ident, "name": name.strip(), "version": 1, "status": "active", "created": now,
               "updated": now, "profiles": [{"version": 1, "name": name.strip(), "persona": persona, "at": now}]}
        self.save(row)
        return self.public(row)

    def update(self, ident, *, name="", persona="", status=""):
        with self.store.transaction():
            row = self.get(ident)
            if status and status not in {"active", "paused", "archived"}:
                raise ValueError("状态支持 active、paused 和 archived")
            current = row["profiles"][-1]
            changed_name, changed_persona = name or row["name"], persona or current["persona"]
            self.validate_profile(changed_name, changed_persona)
            if (changed_name.strip(), changed_persona) != (row["name"], current["persona"]):
                row["version"] += 1
                row["name"] = changed_name.strip()
                row["profiles"].append({"version": row["version"], "name": row["name"],
                                        "persona": changed_persona, "at": time()})
            row.update(status=status or row["status"], updated=time())
            self.save(row)
        return self.public(row)

    def save(self, row):
        self.store.setting(self.prefix + row["id"], row)

    def resolve(self, ident):
        if not isinstance(ident, str) or not ident:
            raise ValueError("请选择名册中的小弟")
        if not AGENT_ID.fullmatch(ident):
            matches = [row["id"] for row in self.list() if row["name"] == ident]
            if len(matches) != 1:
                raise ValueError("名字未找到或重名，请使用名册中的小弟 ID")
            ident = matches[0]
        return ident

    def snapshot(self, ident, guides=()):
        ident = self.resolve(ident)
        row = self.get(ident)
        if row["status"] != "active":
            raise ValueError("这位小弟目前暂停或归档")
        profile = row["profiles"][-1]
        return {"id": ident, "name": profile["name"], "version": profile["version"],
                "persona": profile["persona"], "guides": list(guides)}

    async def chat(self, ident, client, cwd, message):
        """Called under the per-agent chat lock. Recover a prior prompt by its persisted ID."""
        row = self.get(ident)
        if row["status"] != "active":
            raise ValueError("这位小弟目前暂停或归档")
        if getattr(client, "model_route", None) != "go":
            raise DshTaskFailure("go_unavailable", "正式 DSH 的 OpenCode Go 通道暂未就绪。")
        if not row.get("chat_session"):
            row["chat_session"], _ = await _open_session(client, cwd, CHAT_PRESET)
            self.save(row)
        session = row["chat_session"]
        prior = row.get("pending_chat")
        if not prior:
            baseline = await client.go_receipts(session)
            request = {"request_id": uuid4().hex, "session_id": session, "model_route": "go"}
            row["pending_chat"] = {**request, "receipt_count": len(baseline.get("receipts", [])),
                                   "version": row["version"]}
            self.save(row)
            profile = row["profiles"][-1]
            prompt = (f"这是与你持续交谈的大肥鱼。使用她为你写的当前人格（版本 {row['version']}），"
                      f"名字是 {row['name']}。\n<persona>\n{profile['persona']}\n</persona>\n"
                      f"她这轮对你说：\n{message}")
            await _prompt(client, request, prompt)

        request = row["pending_chat"]
        try:
            reply = await _wait_for_turn(client, request)
            if not reply:
                raise DshTaskFailure("empty_reply", "DSH 确认回合已完成，但没有返回正文。")
            bill = await client.go_receipts(session)
            count = len(bill.get("receipts", [])) - request["receipt_count"]
            if bill.get("needs_review") or count <= 0:
                raise DshTaskFailure("billing_unconfirmed", "该回合的 Go 订阅回执未确认。")
        except (DshTaskFailure, GoQuotaWaiting):
            row.pop("pending_chat", None)
            self.save(row)
            raise
        row.pop("pending_chat", None)
        row["last_chat"] = {"request_id": request["request_id"], "version": request["version"], "at": time()}
        self.save(row)
        return {"agent_id": ident, "session_id": session, "request_id": request["request_id"],
                "persona_version": request["version"], "result": reply, "go_receipt_count": count,
                "recovered_previous_turn": bool(prior)}


class DshGuides:
    """Owner-authored reusable guide revisions, independent of roster entries."""

    def __init__(self, store, scope):
        self.store = store
        self.prefix = f"dsh-guide:v1:{scope}:"

    def list(self):
        return [{key: row[key] for key in ("id", "title", "version")} for row in (
            json.loads(item[0])["revisions"][-1] for item in self.store.db.execute(
                "SELECT value FROM settings WHERE substr(key,1,?)=? ORDER BY key",
                (len(self.prefix), self.prefix)))]

    def get(self, ident):
        if not isinstance(ident, str) or not AGENT_ID.fullmatch(ident):
            raise ValueError("请填写有效的指南 ID")
        row = self.store.get_setting(self.prefix + ident)
        if not row:
            raise ValueError("指南不存在")
        return row["revisions"][-1]

    def save(self, ident, title, text):
        if not (isinstance(title, str) and 1 <= len(title.strip()) <= 80
                and isinstance(text, str) and 1 <= len(text.strip()) <= 4000):
            raise ValueError("指南需要标题和正文，分别最多 80 和 4000 字")
        with self.store.transaction():
            if ident:
                self.get(ident)
            else:
                ident = uuid4().hex[:16]
            key = self.prefix + ident
            row = self.store.get_setting(key, {"revisions": []})
            revision = {"id": ident, "title": title.strip(), "text": text.strip(),
                        "version": len(row["revisions"]) + 1}
            row["revisions"].append(revision)
            self.store.setting(key, row)
        return revision

    def select(self, ids):
        if (not isinstance(ids, list) or len(ids) > 3 or any(not isinstance(ident, str) for ident in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("请提供至多三份不同的指南 ID")
        return [self.get(ident) for ident in ids]
