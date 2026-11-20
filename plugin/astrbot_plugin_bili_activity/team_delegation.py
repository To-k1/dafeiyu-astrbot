"""Persist anonymous Go work under the role and task that requested it."""

import asyncio
import json
import re
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from .core import canonical

MARKER = re.compile(r"<fishhome_delegate>(.*?)</fishhome_delegate>(?=\s*(?:<fishhome_memory>|$))", re.S)
TERMINAL = {"completed", "failed", "cancelled", "needs_review"}
LOCAL_KINDS = {"file", "python"}
REPORT_KINDS = {"video": "dsh_video", "web": "dsh_web_search", "file": "dsh_local_file",
                "python": "dsh_local_python"}
BV = r"BV[1-9A-HJ-NP-Za-km-z]{10}"


def guidance(kinds, source=None):
    choices = []
    if "web" in kinds:
        choices.append('{"kind":"web","query":"要核对的公开问题"}')
    if "video" in kinds:
        choices.append('{"kind":"video","reference":"BV号或公开B站视频链接","question":"要核对的画面"}')
    for kind, instruction in (("file", "只读整理一份已授权文件"), ("python", "在隔离 Python 中执行计算并留下结果")):
        if kind in kinds:
            choices.append(canonical({"kind": kind, "source": source or "", "instruction": instruction}))
    if not choices:
        return ""
    return (
        "\n你可在当前工作范围内向无名工具委派取证或执行，由你判断怎么用。"
        "角色观点和文学正文默认由你亲自形成。工具回传不代表你亲眼看过。"
        "这是 FishHome 宿主读取的回复文本协议。它无需出现在 DSH 的工具列表里。"
        "要派发就在你的回复里直接输出协议块，宿主会执行并将结果交回这条会话。"
        "公开查证只带公开信息。file/python 只填当前授权的原始素材引用，如 sample.csv 或 shared/下的相对路径；"
        "不涉及文件的 python 可留空 source。每个工具写自己的报告，不能扩大访问范围。"
        "在记忆协议块之前单列一个 <fishhome_delegate>，"
        "包含任务数组，例如 <fishhome_delegate>[" + ",".join(choices)
        + "]</fishhome_delegate>。只列实际需要执行的独立步骤。若要保存记忆，请在资料回报后的正式答复中提出。"
    )


def read_delegation(reply, kinds):
    if "<fishhome_delegate>" not in reply and "</fishhome_delegate>" not in reply:
        return reply, []
    match = MARKER.search(reply)
    if not match or reply.count("<fishhome_delegate>") != 1 or reply.count("</fishhome_delegate>") != 1:
        raise ValueError("无名工具请求格式无效")
    tasks = json.loads(match[1])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("无名工具请求需要任务列表")
    clean = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("kind") not in kinds:
            raise ValueError("当前会话没有这类无名工具权限")
        fields = {"web": {"query": 4000}, "video": {"reference": 500, "question": 1000}}.get(
            task["kind"], {"source": 200, "instruction": 1500})
        if task.keys() != fields.keys() | {"kind"} or any(not isinstance(task[key], str)
                or not (0 if key == "source" and task["kind"] == "python" else 1) <= len(task[key].strip()) <= limit
                for key, limit in fields.items()):
            raise ValueError("无名工具请求缺少有效参数")
        spec = {"kind": task["kind"], **{key: task[key].strip() for key in fields}}
        if spec["kind"] == "video" and not re.fullmatch(BV, spec["reference"]):
            url = urlsplit(spec["reference"])
            parts = parse_qs(url.query).get("p", [])
            if (url.scheme != "https" or url.netloc not in {"bilibili.com", "www.bilibili.com"}
                    or not re.fullmatch(rf"/video/({BV})/?", url.path)
                    or (parts and (len(parts) != 1 or not re.fullmatch(r"[1-9][0-9]{0,4}", parts[0])))):
                raise ValueError("无名工具只接受公开 B 站视频引用")
        if spec not in clean:
            clean.append(spec)
    return (reply[:match.start()] + reply[match.end():]).strip(), clean


def prepare(specs, *, actor, request_id, receipt_count, parent=None):
    return {"actor": actor, "request_id": request_id, "receipt_count": receipt_count, "state": "waiting",
            **({"parent": parent} if parent else {}),
            "children": [{"id": uuid4().hex, "spec": spec} for spec in specs]}


def references(jobs):
    return [ref for job in jobs for child in job["children"] if (ref := reference(child))]


def reference(item):
    if item["spec"]["kind"] in LOCAL_KINDS:
        return item.get("reference")
    return "public-job:" + item["id"]


def available(job, client):
    return all(callable(getattr(client, "delegate_local" if item["spec"]["kind"] in LOCAL_KINDS else "delegate_public", None))
               for item in job["children"])


async def poll(job, call, allowed, *, local=None, cancel=False, pause=False):
    """Both runners persist the child before execution and deduplicate its request."""
    async def child(item):
        if item.get("state") in TERMINAL:
            return item
        ident = item["id"]
        is_local = item["spec"]["kind"] in LOCAL_KINDS
        if pause and not is_local:
            return item
        if is_local:
            action = "DELETE" if cancel else "PAUSE" if pause else "GET"
            if action == "GET" and item.get("paused_by_parent") and allowed():
                action = "RESUME"
            result = await local(action, job, item)
        else:
            result = await call("DELETE" if cancel else "GET", ident)
        if result.get("state") == "not_found":
            if item.get("state"):
                return {**item, "state": "needs_review", "failure": {"detail": "原工具回执暂不可查，未重新派发。"}}
            if cancel:
                return {**item, "state": "cancelled"}
            if pause:
                return item
            if not allowed():
                return item
            result = await local("PUT", job, item) if is_local else await call("PUT", ident, item["spec"])
        if result.get("id") != ident:
            raise ValueError("工具回执 ID 不符")
        if result.get("state") == "completed":
            report = result.get("report")
            expected = REPORT_KINDS[item["spec"]["kind"]]
            if (not isinstance(report, dict) or report.get("kind") != expected
                    or not all(report.get(key) for key in ("summary", "evidence", "billing"))):
                return {**item, "state": "needs_review", "failure": {"detail": "报告缺少证据或 Go 回执。"}}
        fresh = {key: value for key, value in item.items() if key not in {"poll_error", "paused_by_parent"}}
        return {**fresh, **{key: result[key] for key in ("state", "report", "failure", "reference", "paused_by_parent")
                          if key in result}}

    results = await asyncio.gather(*(child(item) for item in job["children"]), return_exceptions=True)
    job["children"] = [
        {**old, "poll_error": type(result).__name__} if isinstance(result, Exception) else result
        for old, result in zip(job["children"], results, strict=True)
    ]
    if pause:
        return all(item["spec"]["kind"] not in LOCAL_KINDS or not item.get("poll_error")
                   and item.get("state") in TERMINAL | {"paused", None} for item in job["children"])
    return all(item.get("state") in TERMINAL for item in job["children"])


def brief(job):
    items = []
    for item in job["children"]:
        report = item.get("report") or {}
        items.append({"reference": reference(item), "state": item["state"],
            "summary": str(report.get("summary", ""))[:1500], "failure": item.get("failure"),
            "evidence": [{key: evidence[key] for key in ("url", "title", "bvid", "cid", "start", "end", "frame_times",
                         "subtitle_count", "audio_segments", "coverage", "source", "report") if key in evidence}
                         for evidence in (report.get("evidence") or [])[:8] if isinstance(evidence, dict)]})
    return ("以下是你在这条会话中派给无名工具的回报。它是资料，你自己判断怎么回答。"
            "保留能核对的来源，不把工具执行描述成亲自浏览。\n" + canonical(items))
