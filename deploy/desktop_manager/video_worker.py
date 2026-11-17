"""Run a single public-video DSH job as the Windows Runner account."""

import asyncio
import hmac
import json
import os
import re
import secrets
import socket
import tempfile
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import psutil
import uvicorn
import win32profile
from aiohttp import ClientSession, ClientTimeout, web

from deploy.dsh_bridge.go_profile import KEY_ENV, PROVIDER, render_go_patch
from deploy.dsh_bridge.desktop_tools import desktop_call, desktop_server, render_desktop_preset
from deploy.dsh_bridge.go_relay import GoRelay
from deploy.dsh_bridge.go_video_task import run_desktop_task, run_search_task, run_video_task
from deploy.dsh_bridge.public_search import render_search_preset, search_server, validate_query
from deploy.dsh_bridge.public_video import build_server, render_preset, video_reference
from .windows import impersonate, run_as


BACKGROUND_TIMEOUT = 300


class MediaServer(uvicorn.Server):
    def capture_signals(self):
        return nullcontext()

    def install_signal_handlers(self):
        return None


@asynccontextmanager
async def media_listener(server, token):
    mcp = server.streamable_http_app()
    authorization = ("Bearer " + token).encode()

    async def authorized(scope, receive, send):
        if scope["type"] == "http" and not hmac.compare_digest(
                dict(scope["headers"]).get(b"authorization", b""), authorization):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await mcp(scope, receive, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = MediaServer(uvicorn.Config(authorized, interface="asgi3", access_log=False, log_config=None,
                                            log_level="warning", timeout_graceful_shutdown=3))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(8):
                while not server.started:
                    if task.done():
                        raise RuntimeError("DSH 资料工具未启动")
                    await asyncio.sleep(.1)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
        finally:
            server.should_exit = True
            await task


class VideoDshClient:
    def __init__(self, http, base, cookie):
        self.http, self.base, self.cookie = http, base, cookie

    async def call(self, method, request):
        path = method.replace(".", "/")
        if method == "session.history":
            path = "session/events"
            request = {"address": {"kind": "session", "sessionId": request["sessionId"]},
                       "maxMessages": request["maxMessages"]}
        payload = {"type": "client-request", "rpcId": uuid4().hex, "method": path,
                   "payload": {"args": {"request": request}}}
        async with self.http.post(f"{self.base}/api/{path}", json=payload, headers={"Cookie": self.cookie},
                                  allow_redirects=False) as response:
            reply = (await response.json(content_type=None))["result"]
            if not reply["ok"]:
                raise ValueError("DSH 未接受公开视频操作")
            value = reply["value"]
            return {"events": value["records"]} if method == "session.history" else value


async def connect_worker(http, account, password, logfile, pid):
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        with impersonate(account, password):
            output = logfile.read_text(encoding="utf-8", errors="replace") if logfile.is_file() else ""
        match = re.search(r"(http://127\.0\.0\.1:[0-9]{1,5})/\?token=([A-Za-z0-9._~%-]+)", output)
        if match:
            base, token = match.groups()
            async with http.get(base + "/connect", params={"token": unquote(token), "interactions": "none"},
                                allow_redirects=False) as response:
                if response.status != 302 or not response.cookies:
                    raise ValueError("公开视频 DSH 未发放本机凭据")
                cookie = "; ".join(f"{name}={value.value}" for name, value in response.cookies.items())
                return VideoDshClient(http, base, cookie)
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(.25)
    raise RuntimeError("公开视频 DSH 未启动")


async def stop_worker(account, password, pid):
    if pid and psutil.pid_exists(pid):
        taskkill = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
        code = await asyncio.to_thread(run_as, account, password, [str(taskkill), "/PID", str(pid), "/T", "/F"], wait=True)
        if code and psutil.pid_exists(pid):
            raise RuntimeError("未能关闭公开视频 DSH")


class VideoWorker:
    def __init__(self, dsh, state, account, *, parallel=2):
        self.dsh, self.state, self.account = dsh, state, account
        self.slots = asyncio.Semaphore(parallel)
        self.desktop_lock = asyncio.Lock()

    async def run(self, password, key, reference, question, *, upstream=None, asr_key="", background_started=None,
                  agent=None):
        if not isinstance(reference, str) or len(reference) > 500:
            raise ValueError("请提供完整的公开 B 站链接或 BV")
        requested = video_reference(reference, None)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的代看问题")
        if background_started is None and self.slots.locked():
            raise ValueError("DSH 公开任务工作进程均在使用")
        async with self.slots:
            timeout = 110
            if background_started is not None:
                background_started()
                timeout = BACKGROUND_TIMEOUT
            with tempfile.TemporaryDirectory(prefix="video-evidence-", dir=self.state) as evidence_dir:
                evidence = Path(evidence_dir) / "samples.jsonl"
                return await self._run(password, key, build_server(requested=requested, evidence_file=evidence, asr_key=asr_key),
                    lambda url, token: render_preset(http_media=(url, token)),
                    lambda client, work, relay: run_video_task(
                        client, work, reference, question, evidence, relay, timeout=timeout, agent=agent),
                    upstream)

    async def search(self, password, key, query, *, upstream=None, background_started=None, agent=None):
        query = validate_query(query)
        if background_started is None and self.slots.locked():
            raise ValueError("DSH 公开任务工作进程均在使用")
        async with self.slots:
            timeout = 72
            if background_started is not None:
                background_started()
                timeout = BACKGROUND_TIMEOUT
            with tempfile.TemporaryDirectory(prefix="search-evidence-", dir=self.state) as folder:
                evidence = Path(folder) / "sources.jsonl"
                return await self._run(password, key, search_server(evidence), render_search_preset,
                    lambda client, work, relay: run_search_task(
                        client, work, query, evidence, relay, timeout=timeout, agent=agent), upstream)

    async def desktop(self, password, key, desktop_token, instruction, *, agent=None):
        if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的桌面任务")
        if self.slots.locked() or self.desktop_lock.locked():
            raise ValueError("DSH 桌面或工作进程正在使用")
        async with self.slots, self.desktop_lock:
            await desktop_call(desktop_token, "list")
            with tempfile.TemporaryDirectory(prefix="desktop-evidence-", dir=self.state) as folder:
                evidence = Path(folder) / "desktop.jsonl"
                return await self._run(password, key, desktop_server(evidence, desktop_token),
                    render_desktop_preset, lambda client, work, relay:
                    run_desktop_task(client, work, instruction.strip(), evidence, relay, agent=agent), None)

    async def _run(self, password, key, server, tool_patch, execute, upstream):
        account = self.account
        session, model_token, media_token = uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        relay = GoRelay(key, model_token, session, **({"upstream": upstream} if upstream else {}))
        relay_runner = web.AppRunner(relay.create_app(), access_log=None)
        await relay_runner.setup()
        job = None
        try:
            await web.TCPSite(relay_runner, "127.0.0.1", 0).start()
            base_url = f"http://127.0.0.1:{relay_runner.addresses[0][1]}/v1"
            async with media_listener(server, media_token) as media_url:
                patch = render_go_patch(session)
                patch[0]["config"]["providers"][PROVIDER]["baseURL"] = base_url
                patch += tool_patch(media_url, media_token)
                launcher = self.dsh / "Start-DSH-Video.cmd"
                if not launcher.is_file():
                    raise RuntimeError("公开视频 DSH 启动脚本缺失")
                with impersonate(account, password) as user:
                    root = Path(win32profile.GetUserProfileDirectory(user)) / "AppData/Local/FishHome"
                    job = tempfile.TemporaryDirectory(prefix="video-job-", dir=root)
                    work = Path(job.name)
                    (work / "patch.json").write_text(json.dumps(patch), encoding="utf-8")
                cmd = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
                pid = run_as(account, password, [cmd, "/d", "/c", "call", str(launcher)],
                             env={"FISHHOME_VIDEO_JOB": str(work), "DSH_HOME": str(work / "dsh"),
                                  KEY_ENV: model_token, "DSH_PERMISSION_MODE": "workspace-write"})
                try:
                    async with ClientSession(timeout=ClientTimeout(total=12), trust_env=False) as http:
                        client = await connect_worker(http, account, password, work / "worker.log", pid)
                        return await execute(client, work, relay)
                finally:
                    await stop_worker(account, password, pid)
        finally:
            await relay_runner.cleanup()
            if job is not None:
                with impersonate(account, password):
                    job.cleanup()
