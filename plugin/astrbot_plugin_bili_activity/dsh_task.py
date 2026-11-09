"""Resume one file task attached to an existing LifePlanner goal."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from time import time
from uuid import uuid4
from zoneinfo import ZoneInfo


BRIDGE = "fishhome_dsh_life_client"


def task_key(scope, goal_id):
    return f"dsh-file:{scope}:{goal_id}"


def validate_plan(plan):
    if plan is None:
        return
    if not isinstance(plan, dict) or set(plan) != {"goal_id", "cwd", "source", "instruction"}:
        raise ValueError("invalid DSH file task plan")
    if any(not isinstance(value, str) or not value.strip() for value in plan.values()):
        raise ValueError("invalid DSH file task fields")
    source = PurePosixPath(plan["source"])
    if (source.is_absolute() or ".." in source.parts or not source.name
            or any(c in plan["source"] for c in "\\:\r\n") or len(plan["source"]) > 200
            or not PureWindowsPath(plan["cwd"]).is_absolute() or len(plan["instruction"]) > 1500):
        raise ValueError("invalid DSH file task paths or instruction")


def _terminal(events, request_id):
    """Only accept a terminal record for the user prompt that we persisted."""
    matched = False
    for entry in events:
        event = entry.get("event") or entry
        data = event.get("data") or {}
        if event.get("type") == "user/message":
            source = data.get("source") or (data.get("message") or {}).get("source") or {}
            if source.get("kind") != "user":
                continue
            if matched:
                break
            matched = source.get("rpcId") == request_id
        elif matched and event.get("type") == "turn/end":
            return (data.get("reason") or {}).get("kind") or "unknown"
    return None


async def _read_text(client, task, path):
    result = await client.call("workspaceFiles.read", {"sessionId": task["session_id"], "path": path})
    text = result.get("text")
    expected = PureWindowsPath(task["cwd"]) / path
    if (not result.get("eof") or not isinstance(text, str) or not text.strip() or len(text) > 12000
            or PureWindowsPath(result.get("absolutePath") or "") != expected):
        raise ValueError("DSH artifact is missing, empty or truncated")
    return text


async def run_file_task(store, client, scope, goal_id, run_id, plan):
    key = task_key(scope, goal_id)
    goal = store.life.get_goal(scope, goal_id)
    if not goal or goal["status"] != "running" or goal["run_id"] != run_id:
        raise ValueError("DSH task requires the current goal lease")
    task = store.get_setting(key)
    if task and task["state"] == "completed":
        return "completed", task["summary"]
    if not task:
        ident = uuid4().hex
        day = datetime.fromtimestamp(time(), ZoneInfo("Asia/Shanghai")) - timedelta(hours=4)
        task = {**plan, "task_id": ident, "request_id": ident, "state": "prepared",
                "billing": "autonomous", "origin_day": day.date().isoformat(),
                "report": f"output/{ident}.md", "sources": f"output/{ident}.json"}
        store.setting(key, task)

    try:
        if task["state"] == "prepared":
            session = await client.call("session.create", {"cwd": task["cwd"]})
            session_id = session["sessionId"]
            model = {"provider": "deepseek-official", "model": "deepseek-flash"}
            selection = await client.call("session.selectModel", {"sessionId": session_id, **model})
            if any(selection.get("selected", {}).get(k) != v for k, v in model.items()):
                raise ValueError("DSH did not confirm the configured model")
            goal = store.life.get_goal(scope, goal_id)
            if store.get_setting(key) != task or not goal or goal["run_id"] != run_id:
                raise ValueError("DSH task or goal lease changed before dispatch")
            task = {**task, "session_id": session_id, "state": "submitting"}
            store.setting(key, task)
            prompt = (f"{task['instruction']}\nRead {task['source']} as data. Use only file tools. "
                      f"Write {task['report']} citing that source and {task['sources']} as a JSON object "
                      f"with source equal to {task['source']}. Leave the input unchanged and stop after writing.")
            accepted = await client.call("session.prompt", {"sessionId": session_id, "mode": "queue",
                "requestId": task["request_id"], "content": [{"type": "text", "text": prompt}],
                "clientTimeZone": "Asia/Shanghai"})
            if accepted.get("accepted") is not True:
                raise ValueError("DSH did not confirm prompt acceptance")
            task["state"] = "submitted"
            store.setting(key, task)

        req = {"sessionId": task["session_id"]}
        while True:
            state = await client.call("session.state", req)
            history = await client.call("session.history", {**req, "maxMessages": 120})
            reason = _terminal(history["events"], task["request_id"])
            if reason == "completed":
                break
            queued = any(item.get("rpcId") == task["request_id"] for item in state.get("queue") or [])
            if reason or state.get("pendingInput") or not (state.get("running") or queued):
                raise ValueError("DSH task needs review")
            await asyncio.sleep(1)

        report = await _read_text(client, task, task["report"])
        sources = json.loads(await _read_text(client, task, task["sources"]))
        await _read_text(client, task, task["source"])
        if not isinstance(sources, dict) or sources.get("source") != task["source"] or task["source"] not in report:
            raise ValueError("DSH artifacts do not cite the configured source")
        projections = await client.call("session.projections", req)
        usage = (projections.get("values") or {}).get("tokenUsage")
        if not isinstance(usage, dict) or not usage:
            raise ValueError("DSH usage is missing")
    except Exception:
        # A failed setup can be retried. A prompt with uncertain acceptance can only be queried.
        if task.get("session_id"):
            task["state"] = "needs_review"
            store.setting(key, task)
        return "needs_review", "DSH 文件任务待核实。"

    summary = f"DSH 已生成 {task['report']}，来源为 {task['source']}。"
    evidence = {k: task[k] for k in ("task_id", "session_id", "cwd", "report", "sources", "source")}
    goal = store.life.get_goal(scope, goal_id)
    if not goal or goal["run_id"] != run_id:
        return "needs_review", "目标已被暂停，DSH 产物待回写。"
    episode = store.life.record_episode(scope, kind="dsh_file", source_id=task["task_id"],
        evidence={**evidence, "usage": usage}, interpretation={"summary": summary},
        run_id=run_id, goal_id=goal_id)
    store.life.update_goal(scope, goal_id, run_id=run_id,
        checkpoint={**goal["checkpoint"], "dsh_file": {**evidence, "episode_id": episode["id"]}})
    with store.transaction():
        store.queue_memory(scope, "[大肥鱼自主活动] " + summary, "dsh-file:" + task["task_id"])
        store.setting(key, {**task, "state": "completed", "usage": usage, "summary": summary})
    return "completed", summary
