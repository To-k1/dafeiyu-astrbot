"""Owner-requested file jobs using the interaction DSH runner."""

import asyncio
import json
import re
from time import time
from uuid import uuid4

from .core import digest
from .dsh_task import DshTaskFailure, FileInterrupted, GoQuotaWaiting, agent_receipt, execute_file_task, reset_unstarted_go_task, validate_plan

BRIDGE = "fishhome_dsh_commission_client"
PREFIX = "commissioned-file:v1:"
PUBLIC_KINDS = {"video", "web"}
PUBLIC_PENDING = {"prepared", "dispatching", "queued", "running", "cancelling"}


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

    def accept(self, scope, owner, message_id, plan, *, via_agent=False, inline_seconds=0, require_go=False,
               wake_agent=False, agent=None, autonomous=False):
        if not scope or not owner or not message_id:
            raise ValueError("缺少委托来源。")
        validate_plan({"goal_id": "commissioned", **plan})
        identity = [scope, str(message_id)]
        if via_agent:
            identity.extend(("agent", plan["source"], plan["instruction"], plan.get("compute", False)))
            if plan.get("shared"):
                identity.append("shared")
            if plan.get("project"):
                identity.extend(("project", plan["project"]))
        if agent:
            identity.extend(("named", agent_receipt(agent), autonomous))
        ident = digest(identity)[:12]
        key = self._key(scope, ident)
        with self.store.transaction():
            if self.store.get_setting(key):
                return ident
            task_id = uuid4().hex
            self.store.setting(key, {**plan, "id": ident, "task_id": task_id, "request_id": task_id,
                "owner": str(owner), "source_message_id": str(message_id), "recipient": scope,
                "created": time(), "state": "prepared", "delivery": "pending",
                "billing": "autonomous" if autonomous else "interaction",
                **({"agent": agent} if agent else {}), **({"autonomous": True} if autonomous else {}),
                "via_agent": via_agent, "inline_until": time() + inline_seconds if inline_seconds else 0,
                "require_go": require_go, "wake_agent": wake_agent,
                "report": f"output/{task_id}.md", "sources": f"output/{task_id}.json"})
        return ident

    def cancel(self, scope, owner, ident):
        if not re.fullmatch(r"[0-9a-f]{12}", ident):
            return False
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if (not row or row["owner"] != str(owner) or (row.get("via_agent") and not row.get("agent"))
                    or row["state"] not in {"prepared", "paused", "go_wait"}):
                return False
            self.store.setting(key, {**row, "state": "cancelled", "delivery": "cancelled"})
        return True

    def pause_or_resume(self, scope, owner, ident, *, resume=False):
        if not re.fullmatch(r"[0-9a-f]{12}", ident):
            return False
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if not row or row["owner"] != str(owner) or (row.get("via_agent") and not row.get("agent")):
                return False
            if resume and row["state"] == "paused":
                desired = row.pop("paused_from", "prepared")
                row.update(state=desired, delivery="pending")
                if desired == "continuing":
                    row["request_id"] = uuid4().hex
            elif not resume and row["state"] in {"prepared", "go_wait"}:
                row.update(paused_from=row["state"], state="paused", delivery="paused")
            else:
                return False
            self.store.setting(key, row)
        return True

    def request_running_control(self, scope, owner, ident, action):
        if action not in {"pause", "cancel"} or not re.fullmatch(r"[0-9a-f]{12}", ident):
            return False
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if (not row or row["owner"] != str(owner) or (row.get("via_agent") and not row.get("agent"))
                    or row["state"] not in {"submitting", "submitted"}
                    or not row.get("session_id")):
                return False
            if row.get("requested_control") == "cancel" and action == "pause":
                return False
            row["requested_control"] = action
            self.store.setting(key, row)
        return True

    def list(self, scope, owner, *, pending_only=False, ident="", query="", explicit_only=False, job_kind="file",
             public_phase="", unconsumed_only=False, agent_id="", project="", named_only=False):
        prefix = self._key(scope)
        return [json.loads(row["value"]) for row in self.store.db.execute(
            "SELECT value FROM settings WHERE substr(key,1,?)=? AND json_extract(value,'$.owner')=? "
            "AND (?=0 OR json_extract(value,'$.delivery')='pending') "
            "AND (?=0 OR coalesce(json_extract(value,'$.via_agent'),0)=0) "
            "AND (?='all' OR (?='public' AND json_extract(value,'$.job_kind') IN ('video','web')) "
            "OR coalesce(json_extract(value,'$.job_kind'),'file')=?) "
            "AND (?!='running' OR json_extract(value,'$.state') IN ('prepared','dispatching','queued','running','cancelling')) "
            "AND (?!='finished' OR (json_extract(value,'$.state') IN ('completed','failed','needs_review') "
            "AND coalesce(json_extract(value,'$.wake_retry_at'),0)<=?)) "
            "AND (?=0 OR json_extract(value,'$.agent_reply') IS NULL) "
            "AND (?='' OR key=? OR json_extract(value,'$.task_id')=?) "
            "AND (?='' OR instr(lower(value),lower(?))>0) "
            "AND (?='' OR coalesce(json_extract(value,'$.agent.id'),json_extract(value,'$.spec.agent.id'))=?) "
            "AND (?=0 OR coalesce(json_extract(value,'$.agent.id'),json_extract(value,'$.spec.agent.id')) IS NOT NULL) "
            "AND (?='' OR json_extract(value,'$.project')=?) "
            "ORDER BY CASE WHEN ? THEN json_extract(value,'$.created') "
            "ELSE -json_extract(value,'$.created') END LIMIT 10",
            (len(prefix), prefix, str(owner), int(pending_only), int(explicit_only), job_kind, job_kind, job_kind,
             public_phase, public_phase, time(), int(unconsumed_only), ident, prefix + ident, ident,
             query, query, agent_id, agent_id, int(named_only), project, project, int(pending_only)),
        )]

    def accept_public(self, scope, owner, message_id, spec, *, autonomous=False):
        if not scope or not owner or not message_id or spec.get("kind") not in PUBLIC_KINDS:
            raise ValueError("缺少公开资料任务的来源")
        ident = digest([scope, str(message_id), "public", spec, "autonomous"] if autonomous
                       else [scope, str(message_id), "public", spec])[:12]
        key = self._key(scope, ident)
        with self.store.transaction():
            if not self.store.get_setting(key):
                self.store.setting(key, {"id": ident, "task_id": uuid4().hex, "owner": str(owner),
                    "recipient": scope, "created": time(), "job_kind": spec["kind"], "spec": spec,
                    "state": "prepared", "delivery": "pending", "wake_agent": True, "via_agent": True,
                    **({"autonomous": True} if autonomous else {}),
                    "source_message_id": str(message_id), "source": spec.get("reference"),
                    "instruction": spec.get("question") or spec["query"]})
        return self.public_status(self.store.get_setting(key))

    @staticmethod
    def public_status(task):
        return {"id": task["id"], "kind": task["job_kind"], "status": task["state"],
                "source": task["source"], "question": task["instruction"], "created": task["created"],
                "delivery": task["delivery"], "cancellation_requested": bool(task.get("cancel_requested")),
                "summary": task.get("result"), "phase": task.get("worker_phase"),
                "evidence": task.get("media_evidence"), "failure": task.get("failure"),
                **({"agent": agent_receipt(task["spec"]["agent"])} if task["spec"].get("agent") else {})}

    def cancel_public(self, scope, owner, ident):
        rows = self.list(scope, owner, ident=ident, job_kind="public") if ident else []
        if not rows:
            raise ValueError("找不到这项公开资料任务")
        task = rows[0]
        if task["state"] == "prepared":
            task.update(state="cancelled", delivery="cancelled")
        elif task["state"] in {"dispatching", "queued", "running", "cancelling"}:
            task["cancel_requested"] = True
        self.store.setting(self._key(scope, task["id"]), task)
        return self.public_status(task)

    async def _poll_public(self, scope, task, call, record, allowed):
        key = self._key(scope, task["id"])
        if not allowed(task) or task["state"] not in PUBLIC_PENDING:
            return
        if task["state"] == "prepared":
            task["state"] = "dispatching"
            self.store.setting(key, task)
        cancelling = task.get("cancel_requested")
        method = "DELETE" if cancelling else "PUT" if task["state"] == "dispatching" else "GET"
        try:
            result = await call(method, task["task_id"], task["spec"] if method == "PUT" else None)
        except (ValueError, OSError, TimeoutError):
            return
        if not allowed(task) or result.get("id") != task["task_id"]:
            return
        task = self.store.get_setting(key)
        state = result.get("state")
        if state == "not_found":
            state = "needs_review"
            result["failure"] = {"code": "worker_record_missing", "detail": "没有找到原工作进程的任务记录。"}
        if state == "completed":
            try:
                summary, evidence = record(task, result.get("report"))
            except (ValueError, TypeError, KeyError):
                state = "needs_review"
                result["failure"] = {"code": "report_invalid", "detail": "任务没有返回可核查的来源。"}
            else:
                task.update(result=summary, media_evidence=evidence)
        if state not in {"queued", "running", "cancelling", "cancelled", "completed", "failed", "needs_review"}:
            return
        task["state"] = state
        if isinstance(result.get("phase"), str):
            task["worker_phase"] = result["phase"]
        if state not in PUBLIC_PENDING:
            task.pop("cancel_requested", None)
        if state in {"failed", "needs_review"}:
            task["failure"] = result.get("failure")
        if state == "cancelled":
            task["delivery"] = "cancelled"
        self.store.setting(key, task)

    async def dispatch_public(self, scope, owner, call, record, send, allowed, *, ready, wake, enqueue=None):
        rows = self.list(scope, owner, pending_only=True, job_kind="public", public_phase="running")
        await asyncio.gather(*(self._poll_public(scope, task, call, record, allowed) for task in rows))
        finished = self.list(scope, owner, pending_only=True, job_kind="public", public_phase="finished",
                             unconsumed_only=not enqueue and not ready())
        if finished:
            await self._deliver(scope, finished[0], send, allowed, ready, wake, enqueue)

    def inline_result(self, scope, ident):
        key = self._key(scope, ident)
        with self.store.transaction():
            row = self.store.get_setting(key)
            if (not row or row.get("job_kind") or not row.get("via_agent") or row["state"] not in {"completed", "needs_review"}
                    or row["delivery"] not in {"inline", "pending"}):
                return None
            if row["delivery"] == "pending" and row["inline_until"] <= time():
                return None
            self.store.setting(key, {**row, "delivery": "inline"})
        return {"status": row["state"], "task_id": ident, "source": row["source"],
                "report": row["report"] if row["state"] == "completed" else None, "result": row.get("result", ""),
                "failure": row.get("failure")}

    async def dispatch_next(self, scope, owner, make_client, send, still_allowed, *, ready=lambda: True,
                            wake=None, make_autonomous_client=None, enqueue=None):
        rows = [row for row in self.list(scope, owner, pending_only=True)
                if ((row["state"] not in {"completed", "needs_review"} or row.get("inline_until", 0) <= time())
                    and row.get("wake_retry_at", 0) <= time() and still_allowed(row))]
        if not rows:
            return
        task = rows[0]
        key = self._key(scope, task["id"])

        def may_submit():
            return still_allowed(task) and self.store.get_setting(key) == task

        if task["state"] in {"prepared", "submitting", "submitted", "continuing", "go_wait"}:
            try:
                factory = make_autonomous_client if task.get("autonomous") else make_client
                if factory is None:
                    return
                client = factory(scope)
            except (OSError, ValueError, KeyError):
                return
            try:
                try:
                    if task.get("require_go") and getattr(client, "model_route", None) != "go":
                        return
                    if task["state"] == "go_wait":
                        if getattr(client, "model_route", None) != "go" or not await client.go_quota_available():
                            return
                        if not may_submit():
                            return
                        reset_unstarted_go_task(task)
                        self.store.setting(key, task)
                    async with asyncio.timeout(300):
                        def control():
                            row = self.store.get_setting(key, {})
                            return row.get("requested_control", "") if row.get("request_id") == task["request_id"] else ""

                        def save(value):
                            with self.store.transaction():
                                active = self.store.get_setting(key, {})
                                requested = active.get("requested_control") if active.get("request_id") == value["request_id"] else ""
                                self.store.setting(key, {**value, **({"requested_control": requested} if requested else {})})

                        report, usage = await execute_file_task(
                            client, task, save, may_submit, control)
                finally:
                    await client.close()
                task.update(state="completed", result=report, usage=usage)
            except FileInterrupted as exc:
                task.pop("requested_control", None)
                if exc.action == "pause":
                    task.update(state="paused", delivery="paused", paused_from="continuing")
                else:
                    task.update(state="cancelled", delivery="cancelled")
                self.store.setting(key, task)
                return
            except GoQuotaWaiting:
                current = self.store.get_setting(key, {})
                requested = current.pop("requested_control", None)
                if current.get("request_id") == task["request_id"]:
                    task.pop("requested_control", None)
                    task["state"] = "go_wait"
                    if requested == "pause":
                        task.update(state="paused", delivery="paused", paused_from="go_wait")
                    elif requested == "cancel":
                        task.update(state="cancelled", delivery="cancelled")
                    self.store.setting(key, task)
                return
            except Exception as exc:
                current = self.store.get_setting(key)
                if current and current["state"] in {"cancelled", "paused"}:
                    return
                if isinstance(exc, DshTaskFailure):
                    failure = {"code": exc.code, "detail": str(exc)}
                elif isinstance(exc, TimeoutError):
                    if task.get("session_id") and task["state"] in {"submitting", "submitted"}:
                        return  # Next scheduler pass follows the persisted request, without another prompt.
                    failure = {"code": "poll_timeout", "detail": "创建 DSH 会话超时。"}
                else:
                    failure = {"code": "execution_unconfirmed", "detail": "DSH 调用或产物核对未完成。",
                               "exception": type(exc).__name__}
                task.update(state="needs_review", failure=failure)
            task.pop("requested_control", None)
            self.store.setting(key, task)

        await self._deliver(scope, task, send, still_allowed, ready, wake, enqueue)

    def settle_outbox(self, scope, lookup):
        prefix = self._key(scope)
        rows = self.store.db.execute(
            "SELECT key,value FROM settings WHERE substr(key,1,?)=? AND json_extract(value,'$.delivery')='queued'",
            (len(prefix), prefix)).fetchall()
        for key, raw in rows:
            task = json.loads(raw)
            if parts := task.get("delivery_parts"):
                receipts = [lookup(part["queue_id"]) if "queue_id" in part else {"state": "sent", **part}
                            for part in parts]
                if all(receipt and receipt["state"] not in {"pending", "sending"} for receipt in receipts):
                    messages = [str(receipt["message_id"]) for receipt in receipts if receipt.get("message_id")]
                    state = next((receipt["state"] for receipt in receipts if receipt["state"] != "sent"), "sent")
                    self.store.setting(key, {**task, "delivery": state, "message_id": messages[0] if messages else None,
                                             "message_ids": messages})
                continue
            receipt = lookup(task["queue_id"])
            if receipt and receipt["state"] not in {"pending", "sending"}:
                self.store.setting(key, {**task, "delivery": receipt["state"], "message_id": receipt["message_id"]})

    async def _deliver(self, scope, task, send, still_allowed, ready, wake, enqueue=None):
        key = self._key(scope, task["id"])
        if not still_allowed(task) or task.get("inline_until", 0) > time():
            return
        if task.get("wake_agent") and not task.get("agent_reply"):
            if not wake:
                return
            try:
                response = await wake(task)
                if not isinstance(response, str) or not response.strip():
                    raise ValueError("Agent returned an empty response")
            except Exception as exc:
                task.update(wake_retry_at=time() + 60, wake_failure=type(exc).__name__)
                self.store.setting(key, task)
                return
            task.update(agent_reply=response.strip(), agent_consumed_at=time())
            task.pop("wake_retry_at", None)
            task.pop("wake_failure", None)
            self.store.setting(key, task)
        if task.get("autonomous"):
            self.store.setting(key, {**task, "delivery": "internal"})
            return
        if not still_allowed(task) or (not enqueue and not ready()):
            return
        if enqueue and task.get("agent_reply"):
            reply = task["agent_reply"]
            if len(reply) > 2000:
                parts = []
                try:
                    for index, offset in enumerate(range(0, len(reply), 2000)):
                        receipt = await enqueue(scope, reply[offset:offset + 2000], f"{key}:{index}")
                        if queue_id := receipt.get("fishhome_queue_id"):
                            parts.append({"queue_id": queue_id})
                        elif message_id := receipt.get("message_id"):
                            parts.append({"message_id": str(message_id)})
                        else:
                            return
                except (OSError, RuntimeError, ValueError) as exc:
                    if getattr(exc, "state", None):
                        self.store.setting(key, {**task, "delivery": exc.state, "delivery_parts": parts})
                    return
                if any("queue_id" in part for part in parts):
                    self.store.setting(key, {**task, "delivery": "queued", "delivery_parts": parts})
                else:
                    messages = [part["message_id"] for part in parts]
                    self.store.setting(key, {**task, "delivery": "sent", "message_id": messages[0], "message_ids": messages})
                return
            try:
                receipt = await enqueue(scope, reply, key)
            except (OSError, RuntimeError, ValueError) as exc:
                if getattr(exc, "state", None):
                    self.store.setting(key, {**task, "delivery": exc.state})
                return
            if queue_id := receipt.get("fishhome_queue_id"):
                self.store.setting(key, {**task, "delivery": "queued", "queue_id": queue_id})
            elif message_id := receipt.get("message_id"):
                self.store.setting(key, {**task, "delivery": "sent", "message_id": str(message_id)})
            return
        with self.store.transaction():
            current = self.store.get_setting(key)
            if current != task or task["delivery"] != "pending":
                return
            task["delivery"] = "sending"
            self.store.setting(key, task)
        receipt_status = "uncertain"
        try:
            if task.get("wake_agent"):
                text = task["agent_reply"]
            elif task["state"] == "completed":
                source = f"，来源 {task['source']}" if task["source"] else ""
                if task.get("via_agent"):
                    text = f"刚才交给 DSH 的工作做完了{source}。\n\n{task['result']}"
                else:
                    text = f"委托 {task['id']} 已完成{source}，产物 {task['report']}。\n\n{task['result']}"
            elif task.get("via_agent"):
                detail = (task.get("failure") or {}).get("detail", "暂时没有可确认的结果。")
                text = f"刚才交给 DSH 的工作需要核实。{detail}"
            else:
                detail = (task.get("failure") or {}).get("detail", "请在 DSH 查看这项工作。")
                text = f"委托 {task['id']} 需要核实。{detail}"
            receipt = await send(scope, text)
            message_id = receipt.get("message_id")
            if isinstance(message_id, str) and re.fullmatch(r"-?[1-9][0-9]*", message_id):
                task["message_id"] = message_id
                receipt_status = "sent"
        finally:
            self.store.setting(key, {**task, "delivery": receipt_status})
