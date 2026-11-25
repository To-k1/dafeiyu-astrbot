"""Browser access to owner-initiated DSH jobs, independent of the activity ledger."""

import asyncio
import hmac
import re
from html import escape
from pathlib import Path

from aiohttp import web

from deploy.dsh_bridge.public_page import save_public_page

from .public_jobs import JOB_ID, validate_public_job
from .video_transcript import ARTIFACTS, TRANSCRIPT_MODES


PAGE = "/owner-work"
API = "/api/owner-work"
CHAT_PAGE = PAGE + "/chat"
CHAT_API = API + "/chat"
HTML = Path(__file__).with_name("owner_work.html")
KINDS = ("web", "video", "desktop")
ARTIFACT = re.compile(r"web-sources/([0-9a-f]{32}\.md)")
TRANSCRIPT = re.compile(r"transcripts/[0-9a-f]{32}/(" + "|".join(map(re.escape, ARTIFACTS)) + ")")


def validate_job(body):
    if not isinstance(body, dict) or "agent" in body:
        raise ValueError("主人工作台使用无名工作进程")
    if body.get("kind") != "desktop":
        return validate_public_job(body)
    instruction = body.get("instruction")
    if set(body) != {"kind", "instruction"} or not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 1000:
        raise ValueError("请提供 1 至 1000 字的桌面任务说明")
    return {"kind": "desktop", "instruction": instruction.strip()}


def browser_key(token):
    return hmac.digest(token.encode(), b"fishhome-owner-work-browser", "sha256").hex()


def authorize(request, token, port):
    if request.path not in {API, CHAT_API}:
        return False
    if request.method != "GET" and request.headers.get("Origin") != f"http://127.0.0.1:{port}":
        return False
    return hmac.compare_digest(request.cookies.get(f"fishhome-owner-work-{port}", "").encode(),
                               browser_key(token).encode())


def grant(response, token, port):
    response.set_cookie(f"fishhome-owner-work-{port}", browser_key(token), path=API, httponly=True, samesite="Strict")


def page(dsh_ports, target, *, chat=False):
    ancestors = " ".join(f"http://127.0.0.1:{port}" for port in dsh_ports)
    source = HTML.with_name("owner_chat.html") if chat else HTML
    html = source.read_text(encoding="utf-8").replace("__TARGET__", escape(target))
    return web.Response(text=html, content_type="text/html", headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                   "connect-src 'self'; form-action 'none'; frame-ancestors " + ancestors,
    })


def archive_page(directory, content):
    directory.mkdir(parents=True, exist_ok=True)
    return save_public_page(directory, content).removeprefix("shared/")


async def handle(request, jobs, directory, enabled):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    ident = request.query.get("id")
    if request.method == "GET":
        if artifact := request.query.get("artifact"):
            match = ARTIFACT.fullmatch(artifact)
            transcript = TRANSCRIPT.fullmatch(artifact)
            if not (match or transcript):
                raise ValueError("无效的网页正文引用")
            place = (jobs.directory if transcript else directory) / artifact
            if not place.is_file() or place.resolve() != place:
                raise web.HTTPNotFound()
            return web.FileResponse(place, headers={**headers, "Content-Type": (
                                        "text/plain; charset=utf-8" if transcript else "text/markdown; charset=utf-8"),
                                    "Content-Disposition": f'attachment; filename="{(transcript or match)[1]}"',
                                    "X-Content-Type-Options": "nosniff"})
        data = (jobs.get(ident) if ident is not None else
                {"jobs": await asyncio.to_thread(jobs.recent), "enabled": {kind: enabled(kind) for kind in KINDS},
                 "transcript_enabled": jobs.transcript is not None})
        if report_mode := request.query.get("report"):
            full_report = jobs.full_report(ident, data.get("report") or {})
            if not full_report:
                raise web.HTTPNotFound()
            if report_mode == "download":
                return web.Response(text=full_report, content_type="text/markdown", headers={
                    **headers, "Content-Disposition": f'attachment; filename="fishhome-report-{ident}.md"',
                    "X-Content-Type-Options": "nosniff",
                })
            if report_mode != "view":
                raise ValueError("无效的报告读取方式")
            return web.json_response({"text": full_report}, headers=headers)
    else:
        if not ident or not JOB_ID.fullmatch(ident):
            raise ValueError("缺少有效的任务编号")
        if request.method == "PUT":
            body = await request.clone(client_max_size=32 * 1024).json()
            if isinstance(body, dict) and body.get("control") in ("pause", "resume") and body.keys() == {"control"}:
                data = jobs.control(ident, body["control"])
            else:
                local_video = isinstance(body, dict) and body.get("asr_mode") in TRANSCRIPT_MODES and jobs.transcript
                data = jobs.submit(ident, body, enabled=lambda kind: bool(local_video) and kind == "video" or enabled(kind))
        else:
            current = jobs.get(ident)
            if current["state"] == "not_found":
                raise web.HTTPNotFound()
            data = jobs.cancel(ident)
    if report := data.get("report"):
        summary = {key: value for key, value in report.items() if key != "full_report"}
        summary["has_full_report"] = bool(report.get("full_report") or report.get("has_full_report"))
        data = {**data, "report": summary}
    status = 404 if data.get("state") == "not_found" else 200
    return web.json_response(data, status=status, headers=headers)
