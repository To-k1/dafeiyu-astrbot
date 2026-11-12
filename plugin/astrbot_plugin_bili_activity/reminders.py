"""Owner-approved fixed-text reminders in the existing activity store."""

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import digest


SHANGHAI = ZoneInfo("Asia/Shanghai")
PREFIX = "owner-reminder:v1:"


class OwnerReminders:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _key(scope, ident=""):
        return f"{PREFIX}{scope}:{ident}"

    def recover(self):
        self.store.db.execute(
            "UPDATE settings SET value=json_set(value,'$.status','uncertain') "
            "WHERE key LIKE ? AND json_extract(value,'$.status')='sending'", (PREFIX + "%",)
        )

    def create(self, scope, message_id, local_time, text, *, now=None):
        if not message_id or not text.strip() or len(text) > 500:
            raise ValueError("请填写提醒内容，最多 500 字。")
        ident = digest([scope, str(message_id)])[:12]
        key = self._key(scope, ident)
        with self.store.transaction():
            if self.store.get_setting(key):
                return ident
            try:
                due = datetime.strptime(local_time, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI).timestamp()
            except ValueError:
                raise ValueError("请使用北京时间，格式为 YYYY-MM-DD HH:MM。") from None
            if due <= (time.time() if now is None else now):
                raise ValueError("提醒时间须晚于现在。")
            self.store.setting(key, {"id": ident, "due": due, "text": text.strip(), "status": "pending"})
        return ident

    def cancel(self, scope, ident):
        if not re.fullmatch(r"[0-9a-f]{12}", ident):
            return False
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if not row or row["status"] != "pending":
                return False
            self.store.setting(key, {**row, "status": "cancelled"})
        return True

    def list(self, scope):
        prefix = self._key(scope)
        return [json.loads(row[0]) for row in self.store.db.execute(
            "SELECT value FROM settings WHERE substr(key,1,?)=? "
            "ORDER BY json_extract(value,'$.due') DESC LIMIT 10", (len(prefix), prefix)
        )]

    async def dispatch_due(self, scope, send, *, now=None, ready=lambda: True):
        prefix = self._key(scope)
        with self.store.transaction():
            found = self.store.db.execute(
                "SELECT key,value FROM settings WHERE substr(key,1,?)=? "
                "AND json_extract(value,'$.status')='pending' AND json_extract(value,'$.due')<=? "
                "ORDER BY json_extract(value,'$.due'),key LIMIT 1",
                (len(prefix), prefix, time.time() if now is None else now),
            ).fetchone()
            if found is None or not ready():
                return
            key, raw = found
            row = json.loads(raw)
            self.store.setting(key, {**row, "status": "sending"})
        result = {**row, "status": "uncertain"}
        try:
            receipt = await send(scope, "提醒：" + row["text"])
            message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
            if isinstance(message_id, str) and re.fullmatch(r"-?[1-9][0-9]*", message_id):
                result |= {"status": "sent", "message_id": message_id}
        except (TimeoutError, OSError):
            pass
        finally:
            self.store.setting(key, result)
