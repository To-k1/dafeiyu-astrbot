"""Run each public-data job in its own DSH workspace under the Windows Runner account."""

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
from deploy.dsh_bridge.go_relay import GoRelay, GoSubscriptionWaiting
from deploy.dsh_bridge.go_video_task import GoReportError, run_desktop_task, run_search_task, run_video_task
from deploy.dsh_bridge.public_search import render_search_preset, search_server, validate_task_query
from deploy.dsh_bridge.public_video import build_server, render_preset, video_reference
from deploy.bili_activity.resource_capacity import MAX_WAIT_SECONDS, wait_for_memory
from .windows import impersonate, run_as


BACKGROUND_TIMEOUT = 300
BACKGROUND_STARTUP_TIMEOUT = 90


class WorkerStartupError(RuntimeError):
    pass


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
            reply = (await response.json(content_type=None)).get("result")
            if not isinstance(reply, dict) or not reply.get("ok"):
                raise GoReportError("worker_rpc_rejected", f"DSH 未接受公开资料操作：{method}。")
            value = reply["value"]
            return {"events": value["records"]} if method == "session.history" else value


async def connect_worker(http, account, password, logfile, pid, *, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with impersonate(account, password):
            output = logfile.read_text(encoding="utf-8", errors="replace") if logfile.is_file() else ""
        match = re.search(r"(http://127\.0\.0\.1:[0-9]{1,5})/\?token=([A-Za-z0-9._~%-]+)", output)
        if match:
            base, token = match.groups()
            async with http.get(base + "/connect", params={"token": unquote(token), "interactions": "none"},
                                allow_redirects=False) as response:
                if response.status != 302 or not response.cookies:
                    raise WorkerStartupError("工作进程未发放本机连接凭据")
                cookie = "; ".join(f"{name}={value.value}" for name, value in response.cookies.items())
                return VideoDshClient(http, base, cookie)
        if not psutil.pid_exists(pid):
            raise WorkerStartupError("工作进程在就绪前退出")
        await asyncio.sleep(.25)
    raise TimeoutError("等待公开资料 DSH 工作进程就绪超时")


async def stop_worker(account, password, pid):
    if pid and psutil.pid_exists(pid):
        taskkill = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
        code = await asyncio.to_thread(run_as, account, password, [str(taskkill), "/PID", str(pid), "/T", "/F"], wait=True)
        if code and psutil.pid_exists(pid):
            raise RuntimeError("未能关闭公开视频 DSH")


class VideoWorker:
    def __init__(self, dsh, state, account):
        self.dsh, self.state, self.account = dsh, state, account
        self.desktop_lock = asyncio.Lock()
        self.cold_starts = asyncio.Semaphore(3)

    async def run(self, password, key, reference, question, *, upstream=None, asr_key="", background_started=None,
                  agent=None, language=""):
        if not isinstance(reference, str) or len(reference) > 500:
            raise ValueError("请提供完整的公开 B 站链接或 BV")
        requested = video_reference(reference, None)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的代看问题")
        timeout = 110
        if background_started is not None:
            timeout = BACKGROUND_TIMEOUT
        with tempfile.TemporaryDirectory(prefix="video-evidence-", dir=self.state) as evidence_dir:
            evidence = Path(evidence_dir) / "samples.jsonl"
            return await self._run(password, key, build_server(requested=requested, evidence_file=evidence, asr_key=asr_key),
                lambda url, token: render_preset(http_media=(url, token)),
                lambda client, work, relay: run_video_task(
                    client, work, reference, question, evidence, relay, timeout=timeout, agent=agent, language=language,
                    progress=background_started),
                upstream, progress=background_started,
                startup_timeout=BACKGROUND_STARTUP_TIMEOUT if background_started is not None else 30)

    async def search(self, password, key, query, *, upstream=None, background_started=None, agent=None,
                     archive_page=None, language=""):
        query = validate_task_query(query)
        timeout = 72
        if background_started is not None:
            background_started()
            timeout = BACKGROUND_TIMEOUT
        with tempfile.TemporaryDirectory(prefix="search-evidence-", dir=self.state) as folder:
            evidence = Path(folder) / "sources.jsonl"
            return await self._run(password, key, search_server(evidence, archive_page), render_search_preset,
                lambda client, work, relay: run_search_task(
                    client, work, query, evidence, relay, timeout=timeout, agent=agent, language=language,
                    progress=background_started), upstream, progress=background_started,
                startup_timeout=BACKGROUND_STARTUP_TIMEOUT if background_started is not None else 30)

    async def desktop(self, password, key, desktop_token, instruction, *, agent=None, language=""):
        if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的桌面任务")
        async with self.desktop_lock:
            await desktop_call(desktop_token, "list")
            with tempfile.TemporaryDirectory(prefix="desktop-evidence-", dir=self.state) as folder:
                evidence = Path(folder) / "desktop.jsonl"
                return await self._run(password, key, desktop_server(evidence, desktop_token),
                    render_desktop_preset, lambda client, work, relay:
                    run_desktop_task(client, work, instruction.strip(), evidence, relay, agent=agent, language=language), None)

    async def _run(self, password, key, server, tool_patch, execute, upstream, *, progress=None, startup_timeout=30):
        account = self.account
        session, model_token, media_token = uuid4().hex, secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        relay = GoRelay(key, model_token, session, **({"upstream": upstream} if upstream else {}))
        relay_runner = web.AppRunner(relay.create_app(), access_log=None)
        await relay_runner.setup()
        job = None
        pid = None
        try:
            if progress:
                progress("starting_tools")
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
                try:
                    async with ClientSession(timeout=ClientTimeout(total=12), trust_env=False) as http:
                        if progress:
                            progress("waiting_local_resources")
                        try:
                            async with asyncio.timeout(MAX_WAIT_SECONDS):
                                await self.cold_starts.acquire()
                                try:
                                    await wait_for_memory()
                                except BaseException:
                                    self.cold_starts.release()
                                    raise
                        except TimeoutError:
                            raise ValueError("本机 DSH 启动仍在排队，或可用内存低于 3 GiB，请稍后再试") from None
                        try:
                            cmd = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
                            for attempt in range(2):
                                logfile = work / "worker.log"
                                pid = run_as(account, password, [cmd, "/d", "/c", "call", str(launcher)],
                                             env={"FISHHOME_VIDEO_JOB": str(work), "DSH_HOME": str(work / "dsh"),
                                                  KEY_ENV: model_token, "DSH_PERMISSION_MODE": "workspace-write"})
                                if progress:
                                    progress("connecting_worker" if not attempt else "restarting_worker")
                                try:
                                    client = await connect_worker(http, account, password, logfile, pid,
                                                                  timeout=startup_timeout)
                                    break
                                except WorkerStartupError as exc:
                                    if attempt:
                                        raise GoReportError("worker_startup_failed", str(exc)) from exc
                                    # No session or model request exists at this point.
                                    await stop_worker(account, password, pid)
                                    pid = None
                                    with impersonate(account, password):
                                        logfile.unlink(missing_ok=True)
                                except TimeoutError as exc:
                                    raise GoReportError("worker_startup_timed_out", str(exc)) from exc
                        finally:
                            self.cold_starts.release()
                        return await execute(client, work, relay)
                except (ValueError, TimeoutError) as exc:
                    if relay.status in {402, 429} and not relay.receipts:
                        raise GoSubscriptionWaiting("OpenCode Go 等待订阅额度恢复") from exc
                    raise
                finally:
                    await stop_worker(account, password, pid)
        finally:
            await relay_runner.cleanup()
            if job is not None:
                with impersonate(account, password):
                    job.cleanup()
