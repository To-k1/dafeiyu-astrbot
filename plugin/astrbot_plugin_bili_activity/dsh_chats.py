"""Persistent, owner-scoped queue for direct DSH conversations."""
import asyncio
import json
import re
from time import time
from uuid import uuid4

from .dsh_roster import DshRoster
from .dsh_task import CHAT_PRESET, DshTaskFailure, GoQuotaWaiting, _open_session, _prompt, _wait_for_turn

PREFIX = "dsh-chat:v1:"
ACTIVE = {"queued", "running", "go_wait"}


class DshChats:
    def __init__(self, store, scope):
        self.store, self.scope = store, scope
        self.prefix = f"{PREFIX}{scope}:"

    def _rows(self):
        return [json.loads(row[0]) for row in self.store.db.execute(
            "SELECT value FROM settings WHERE substr(key,1,?)=? ORDER BY json_extract(value,'$.sequence'), key",
            (len(self.prefix), self.prefix))]

    def _save(self, row):
        self.store.setting(self.prefix + row["id"], row)

    def get(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{16}", ident):
            raise ValueError("请使用已有聊天任务 ID")
        row = self.store.get_setting(self.prefix + ident)
        if row is None:
            raise ValueError("找不到聊天任务")
        return row

    def update_notification(self, ident, **fields):
        """Persist notification preparation before attempting external delivery."""
        if set(fields) - {"agent_reply"}:
            raise ValueError("无效的通知字段")
        with self.store.transaction():
            row = self.get(ident)
            row.update(fields)
            self._save(row)
        return row

    def list(self):
        return list(reversed(self._rows()))

    def _next_sequence(self):
        return max((row.get("sequence", 0) for row in self._rows()), default=0) + 1

    def _adopt(self, roster_row):
        pending = roster_row.get("pending_chat")
        if not pending or any(row.get("turn", {}).get("request_id") == pending["request_id"] for row in self._rows()):
            return
        self._save({"id": uuid4().hex[:16], "agent_id": roster_row["id"], "message": None,
                    "source_id": None, "autonomous": False, "legacy": True, "created": time(), "sequence": self._next_sequence(),
                    "status": "running", "turn": pending, "delivery": "pending"})

    def recover(self):
        """Adopt old roster turns and mark interrupted notification delivery uncertain."""
        roster = DshRoster(self.store, self.scope)
        with self.store.transaction():
            for profile in roster.list():
                self._adopt(roster.get(profile["id"]))
            for row in self._rows():
                if row.get("delivery") == "sending":
                    row["delivery"] = "uncertain"
                    self._save(row)

    def enqueue(self, message, agent_id, source_id, autonomous=False):
        if not isinstance(message, str) or not message.strip() or len(message) > 2000 or not source_id:
            raise ValueError("请提供 1–2000 字的纯文本消息和来源")
        if type(autonomous) is not bool:
            raise ValueError("无效的聊天来源")
        roster = DshRoster(self.store, self.scope)
        if agent_id:
            agent_id = roster.resolve(agent_id)
            roster.snapshot(agent_id)
        else:
            agent_id = ""
        with self.store.transaction():
            if agent_id:
                self._adopt(roster.get(agent_id))
            previous = next((row for row in self._rows() if row.get("source_id") == str(source_id)
                             and row["agent_id"] == agent_id and row["message"] == message.strip()), None)
            if previous:
                return previous
            row = {"id": uuid4().hex[:16], "agent_id": agent_id, "message": message.strip(),
                   "source_id": str(source_id), "autonomous": autonomous, "legacy": False,
                   "created": time(), "sequence": self._next_sequence(), "status": "queued", "delivery": "pending"}
            self._save(row)
        return row

    async def _run(self, ident, make_client, workspace, allowed, chat_lock):
        row = self.get(ident)
        # The caller supplies the same per-agent lock used by roster updates.
        async with chat_lock(self.scope, row["agent_id"] or row["id"]):
            row = self.get(ident)
            if row["status"] not in ACTIVE or not allowed(row):
                return
            client = make_client(row)
            try:
                if getattr(client, "model_route", None) != "go":
                    return
                if row["status"] == "go_wait" and not await client.go_quota_available():
                    return
                try:
                    async with asyncio.timeout(75):
                        await self._turn(row, client, workspace(row))
                except TimeoutError:
                    pass  # The stored request is polled on the next dispatch.
                except GoQuotaWaiting as exc:
                    with self.store.transaction():
                        row = self.get(ident)
                        if row.get("legacy"):
                            row.update(status="needs_review", failure={"code": "go_wait", "detail": str(exc)})
                            self._save(row)
                            return
                        turn = row.pop("turn", None)
                        if turn:
                            row.setdefault("attempts", []).append(turn)
                            if row["agent_id"]:
                                roster = DshRoster(self.store, self.scope)
                                profile = roster.get(row["agent_id"])
                                if profile.get("pending_chat", {}).get("request_id") == turn["request_id"]:
                                    profile.pop("pending_chat", None)
                                    roster.save(profile)
                        row.update(status="go_wait", failure={"code": "go_wait", "detail": str(exc)})
                        self._save(row)
                except DshTaskFailure as exc:
                    row = self.get(ident)
                    row.update(status="needs_review", failure={"code": exc.code, "detail": str(exc)})
                    self._save(row)
            finally:
                await client.close()

    async def _turn(self, row, client, cwd):
        roster = DshRoster(self.store, self.scope)
        agent_id = row["agent_id"]
        turn = row.get("turn")
        if not turn:
            profile = roster.get(agent_id) if agent_id else None
            if profile and profile["status"] != "active":
                raise DshTaskFailure("inactive", "这位小弟目前暂停或归档")
            session = profile.get("chat_session") if profile else None
            if not session:
                session, _ = await _open_session(client, cwd, CHAT_PRESET)
                if profile:
                    profile["chat_session"] = session
                    roster.save(profile)
            baseline = await client.go_receipts(session)
            turn = {"session_id": session, "request_id": uuid4().hex, "model_route": "go",
                    "receipt_count": len(baseline.get("receipts", [])),
                    "version": profile["version"] if profile else None}
            with self.store.transaction():
                row = self.get(row["id"])
                row.update(turn=turn, status="running")
                self._save(row)
                if profile:
                    profile = roster.get(agent_id)
                    profile["pending_chat"] = turn
                    roster.save(profile)
            if profile:
                persona = profile["profiles"][-1]
                text = (f"这是与你持续交谈的大肥鱼。使用她为你写的当前人格（版本 {turn['version']}），"
                        f"名字是 {profile['name']}。\n<persona>\n{persona['persona']}\n</persona>\n"
                        f"她这轮对你说：\n{row['message']}")
            else:
                text = row["message"]
            await _prompt(client, turn, text)
        reply = await _wait_for_turn(client, turn)
        if not reply:
            raise DshTaskFailure("empty_reply", "DSH 确认回合已完成，但没有返回正文。")
        bill = await client.go_receipts(turn["session_id"])
        count = len(bill.get("receipts", [])) - turn["receipt_count"]
        if bill.get("needs_review") or count <= 0:
            raise DshTaskFailure("billing_unconfirmed", "该回合的 Go 订阅回执未确认。")
        with self.store.transaction():
            row = self.get(row["id"])
            row.update(status="completed", result=reply, request_id=turn["request_id"],
                       session_id=turn["session_id"], persona_version=turn.get("version"),
                       go_receipt_count=count, completed=time())
            row.pop("failure", None)
            self._save(row)
            if agent_id:
                profile = roster.get(agent_id)
                if profile.get("pending_chat", {}).get("request_id") == turn["request_id"]:
                    profile.pop("pending_chat", None)
                    profile["last_chat"] = {"request_id": turn["request_id"],
                                            "version": turn.get("version"), "at": time()}
                    roster.save(profile)

    async def dispatch_next(self, make_client, workspace, allowed, notify, chat_lock):
        if notify:
            pending = [row for row in self._rows() if row["status"] in {"completed", "needs_review"}
                       and row.get("delivery") == "pending" and allowed(row)]
            await asyncio.gather(*(self._notify(row["id"], notify) for row in pending))
        heads = {}
        for row in self._rows():
            if row["status"] in ACTIVE | {"needs_review"}:
                key = row["agent_id"] or row["id"]
                heads.setdefault(key, row)
        eligible = [row for row in heads.values() if row["status"] in ACTIVE and allowed(row)]

        async def dispatch_one(row):
            try:
                await self._run(row["id"], make_client, workspace, allowed, chat_lock)
            except Exception as exc:
                with self.store.transaction():
                    current = self.get(row["id"])
                    current["poll"] = {"error_type": type(exc).__name__, "checked_at": time()}
                    self._save(current)
            current = self.get(row["id"])
            if (notify and current["status"] in {"completed", "needs_review"}
                    and current.get("delivery") == "pending" and allowed(current)):
                await self._notify(row["id"], notify)

        return await asyncio.gather(*(dispatch_one(row) for row in eligible))
    async def _notify(self, ident, notify):
        with self.store.transaction():
            row = self.get(ident)
            if row.get("delivery") != "pending":
                return
            row["delivery"] = "sending"
            self._save(row)
        error = None
        try:
            summary = await notify(row)
        except Exception as exc:
            summary = None
            error = {"error_type": type(exc).__name__, "checked_at": time()}
        with self.store.transaction():
            row = self.get(ident)
            if summary is None:
                row["delivery"] = "pending"
                if error:
                    row["notification_error"] = error
            else:
                row.update(delivery="notified", notification=summary)
                row.pop("notification_error", None)
            self._save(row)





