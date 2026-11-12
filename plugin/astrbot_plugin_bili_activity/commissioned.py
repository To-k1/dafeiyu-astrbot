"""Owner-requested file jobs using the interaction DSH runner."""

import asyncio
import json
import re
from time import time
from uuid import uuid4

from .core import digest
from .dsh_task import execute_file_task, validate_plan

BRIDGE = "fishhome_dsh_commission_client"
PREFIX = "commissioned-file:v1:"


class CommissionedFiles:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _key(scope, ident=""):
        return f"{PREFIX}{scope}:{ident}"

    def recover(self):
        self.store.db.execute(
            "UPDATE settings SET value=json_set(value,'$.delivery','uncertain') "
            "WHERE key LIKE ? AND json_extract(value,'$.delivery')='sending'", (PREFIX + "%",)
        )

    def accept(self, scope, owner, message_id, plan):
        if not scope or not owner or not message_id:
            raise ValueError("缺少委托来源。")
        validate_plan({"goal_id": "commissioned", **plan})
        ident = digest([scope, str(message_id)])[:12]
        key = self._key(scope, ident)
        with self.store.transaction():
            if self.store.get_setting(key):
                return ident
            task_id = uuid4().hex
            self.store.setting(key, {**plan, "id": ident, "task_id": task_id, "request_id": task_id,
                "owner": str(owner), "source_message_id": str(message_id), "recipient": scope,
                "created": time(), "state": "prepared", "delivery": "pending", "billing": "interaction",
                "report": f"output/{task_id}.md", "sources": f"output/{task_id}.json"})
        return ident

    def cancel(self, scope, owner, ident):
        if not re.fullmatch(r"[0-9a-f]{12}", ident):
            return False
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if not row or row["owner"] != str(owner) or row["state"] != "prepared":
                return False
            self.store.setting(key, {**row, "state": "cancelled", "delivery": "cancelled"})
        return True

    def list(self, scope, owner, *, pending_only=False):
        prefix = self._key(scope)
        return [json.loads(row["value"]) for row in self.store.db.execute(
            "SELECT value FROM settings WHERE substr(key,1,?)=? AND json_extract(value,'$.owner')=? "
            "AND (?=0 OR json_extract(value,'$.delivery')='pending') "
            "ORDER BY CASE WHEN ? THEN json_extract(value,'$.created') "
            "ELSE -json_extract(value,'$.created') END LIMIT 10",
            (len(prefix), prefix, str(owner), int(pending_only), int(pending_only)),
        )]

    async def dispatch_next(self, scope, owner, make_client, send, still_allowed, *, ready=lambda: True):
        rows = self.list(scope, owner, pending_only=True)
        if not rows or not still_allowed(rows[0]):
            return
        task = rows[0]
        key = self._key(scope, task["id"])

        def may_submit():
            return still_allowed(task) and self.store.get_setting(key) == task

        if task["state"] in {"prepared", "submitting", "submitted"}:
            try:
                client = make_client(scope)
                try:
                    async with asyncio.timeout(300):
                        report, usage = await execute_file_task(
                            client, task, lambda value: self.store.setting(key, value), may_submit)
                finally:
                    await client.close()
                task.update(state="completed", result=report[:1200], usage=usage)
            except Exception:
                current = self.store.get_setting(key)
                if current and current["state"] == "cancelled":
                    return
                task["state"] = "needs_review"
            self.store.setting(key, task)

        if not still_allowed(task) or not ready():
            return
        with self.store.transaction():
            current = self.store.get_setting(key)
            if current != task or task["delivery"] != "pending":
                return
            task["delivery"] = "sending"
            self.store.setting(key, task)
        receipt_status = "uncertain"
        try:
            if task["state"] == "completed":
                text = f"委托 {task['id']} 已完成，来源 {task['source']}，产物 {task['report']}。\n\n{task['result']}"
            else:
                text = f"委托 {task['id']} 需要核实。请查看 DSH 的任务和交互额度，确认前不会自动再次执行。"
            receipt = await send(scope, text)
            message_id = receipt.get("message_id")
            if isinstance(message_id, str) and re.fullmatch(r"-?[1-9][0-9]*", message_id):
                task["message_id"] = message_id
                receipt_status = "sent"
        finally:
            self.store.setting(key, {**task, "delivery": receipt_status})
