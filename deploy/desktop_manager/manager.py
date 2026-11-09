"""Local WebUI and process supervision for FishHome's Windows tray app."""

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import psutil
import win32profile
from aiohttp import ClientSession, ClientTimeout, web

from .windows import Credentials, impersonate, run_as, write_json


PORT = 6196
RUNNER = "FishHomeRunner"
ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).with_name("index.html")


@dataclass
class Service:
    name: str
    cwd: Path
    command: tuple
    port: int
    marker: str
    url: str = ""
    env: dict = field(default_factory=dict)
    runner: bool = False


class Manager:
    def __init__(self, dev, state):
        self.dev, self.state = dev, state
        state.mkdir(parents=True, exist_ok=True)
        self.creds = Credentials(state / "credentials.bin")
        for bucket in ("interaction", "autonomous"):
            self.creds.values.setdefault(bucket, secrets.token_urlsafe(32))
        self.creds.save()
        options = state / "settings.json"
        self.options = json.loads(options.read_text(encoding="utf-8-sig")) if options.is_file() else {}
        self.launch_file = state / "processes.json"
        self.launches = json.loads(self.launch_file.read_text(encoding="utf-8")) if self.launch_file.is_file() else {}
        self.astro = dev / "astrbot-4.29.4"
        self.python = self.astro / ".venv/Scripts/python.exe"
        self.data = self.astro / "data/plugin_data"
        self.bridge_settings = self.data / "astrbot_plugin_dsh_bridge/m3.json"
        self.activity = self.data / "astrbot_plugin_bili_activity"
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.emulator_requested = 0.0

    def specs(self):
        cat, phone, ollama = (self.dev / name for name in ("napcat-4.18.38", "PhoneMCP", "ollama-0.40.15"))
        dsh = self.dev / "dsh-0.2.2-rc.1"
        emulator = Path(r"C:\leidian\LDPlayer14")
        cmd = Path(os.environ["ComSpec"])
        qq = str(self.options.get("napcat_qq", ""))
        cat_args = ("-q", qq) if qq.isascii() and qq.isdigit() else ()
        key = self.creds.values.get("upstream_key")
        cost_env = {"FISH_COST_INTERACTION_TOKEN": self.creds.values["interaction"],
                    "FISH_COST_AUTONOMOUS_TOKEN": self.creds.values["autonomous"]}
        if key:
            cost_env["FISH_COST_UPSTREAM_KEY"] = key
        dsh_env = {}
        if self.options.get("metered_dsh") is True:
            dsh_env = {"DEEPSEEK_BASE_URL": "http://127.0.0.1:6197/anthropic/autonomous",
                       "FISHHOME_DSH_GATEWAY_TOKEN": self.creds.values["autonomous"],
                       "DEEPSEEK_API_KEY": self.creds.values["autonomous"]}
        return {
            "budget": Service("人民币计费", ROOT, (self.python, "-B", "-m", "deploy.fish_cost", "--db",
                self.activity / "activity.sqlite3", "serve", *(() if key else ("--offline",))), 6197,
                "deploy.fish_cost", env=cost_env),
            "ollama": Service("Ollama", ollama, (ollama / "ollama.exe", "serve"), 11434, "ollama.exe",
                env={"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_MODELS": str(self.dev / "ollama-models"),
                     "OLLAMA_VULKAN": "1", "OLLAMA_NO_CLOUD": "1"}),
            "emulator": Service("雷电模拟器", emulator, (emulator / "ldconsole.exe",), 0, ""),
            "phone": Service("PhoneMCP", phone / "src", (phone / "venv/Scripts/python.exe", "main.py", "serve",
                "--transport", "sse", "--host", "127.0.0.1", "--port", "8009", "--path", "/Phone"), 8009,
                "main.py serve", env={"PATH": r"C:\leidian\LDPlayer14;" + os.environ["PATH"]}),
            "dsh": Service("DeepSeek Harness", dsh, (cmd, "/d", "/c", "call", dsh / "Start-DSH-M3.cmd"),
                59934, str(dsh / "node_modules/@deepseek-ai/dsh/lib/bin.js"), env=dsh_env, runner=True),
            "astrbot": Service("AstrBot", self.astro, (self.python, "-B", "-m", "astrbot.cli", "run"), 6185,
                "astrbot.cli run", "http://127.0.0.1:6185/",
                env={"FISHHOME_INTERACTION_TOKEN": self.creds.values["interaction"]}),
            "napcat": Service("NapCat", cat, (cat / "node.exe", cat / "index.js", *cat_args), 6099,
                str(cat / "index.js"), "http://127.0.0.1:6099/"),
            "room": Service("房间与记忆星海", ROOT, (self.python, "-B", "-m", "deploy.memory_observatory.server",
                "--port", "8767", "--astrbot-root", self.astro, "--life-data-dir", self.activity), 8767,
                "deploy.memory_observatory.server", "http://127.0.0.1:8767/room"),
        }

    @staticmethod
    def listeners():
        listeners = {}
        for entry in psutil.net_connections("tcp"):
            if entry.status == psutil.CONN_LISTEN and entry.laddr:
                listeners.setdefault(entry.laddr.port, set()).add(entry.pid)
        return listeners

    def runner_password(self):
        return self.creds.values.get("runner_password")

    def matching_pid(self, service, pid):
        if not pid:
            return None
        def inspect():
            process = psutil.Process(pid)
            if service.name == "NapCat":
                marker = service.marker.casefold()
                for candidate in [process, *process.parents()]:
                    if any(arg.casefold().replace("/", "\\") == marker for arg in candidate.cmdline()):
                        return candidate.pid
                return None
            args = " ".join(process.cmdline()).casefold().replace("/", "\\")
            if service.marker.casefold().replace("/", "\\") not in args:
                return None
            if service.runner:
                return pid  # This marker is a path inside the pinned install.
            if service.name == "Ollama":
                if Path(process.exe()).resolve().parent in {service.cwd.resolve(), (self.dev / "ollama").resolve()}:
                    return pid
                return None
            return pid if Path(process.cwd()).resolve() == service.cwd.resolve() else None
        try:
            return inspect()
        except psutil.AccessDenied:
            if service.runner and self.runner_password():
                try:
                    with impersonate(RUNNER, self.runner_password()):
                        return inspect()
                except (OSError, psutil.Error):
                    return None
        except psutil.Error:
            return None
        return None

    def emulator_status(self, service):
        if not service.command[0].is_file():
            return "stopped"
        try:
            result = subprocess.run([str(service.command[0]), "list2"], capture_output=True, timeout=3,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return "starting"
        for line in result.stdout.decode("gbk", errors="replace").splitlines():
            fields = line.split(",")
            if len(fields) >= 7 and fields[0] == "0":
                if fields[4] == "1":
                    return "running"
                if fields[5].isdigit():
                    return "starting"
        return "starting" if time.monotonic() - self.emulator_requested < 15 else "stopped"

    async def emulator_action(self, service, action):
        if not service.command[0].is_file():
            raise ValueError("未找到雷电模拟器")
        if action == "launch":
            # Use the installed GUI's startup path so it registers emulator-5554 with ADB.
            subprocess.Popen([str(service.cwd / "dnplayer.exe")], cwd=service.cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            self.emulator_requested = time.monotonic()
            return "已发出模拟器启动请求"
        child = await asyncio.create_subprocess_exec(str(service.command[0]), action, "--index", "0",
            cwd=service.cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if await child.wait():
            raise RuntimeError("雷电模拟器操作失败")
        self.emulator_requested = 0.0
        return "已请求关闭模拟器"

    def tracked_pid(self, name):
        known = self.launches.get(name)
        if known:
            try:
                process = psutil.Process(known["pid"])
                if process.create_time() == known["time"]:
                    return process.pid
            except psutil.Error:
                pass
        return None

    def launching_pid(self, service):
        name = service.command[0].name.casefold()
        names = {name}
        if service.runner:
            names.add("node.exe")
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").casefold() in names:
                match = self.matching_pid(service, proc.pid)
                if match:
                    return match
        return None

    def statuses(self):
        listeners = self.listeners()
        rows = []
        for name, service in self.specs().items():
            if name == "emulator":
                status = self.emulator_status(service)
                rows.append({"id": name, "name": service.name, "port": 0, "state": status,
                             "stoppable": status != "stopped", "external": False, "recognized": True, "url": ""})
                continue
            pids = listeners.get(service.port, set())
            owned = self.tracked_pid(name)
            recognized = [pid for pid in pids if self.matching_pid(service, pid)]
            status = "running" if pids else "starting" if owned else "stopped"
            rows.append({"id": name, "name": service.name, "port": service.port, "state": status,
                         "stoppable": bool(owned or recognized), "external": bool(pids and not owned),
                         "recognized": bool(recognized), "url": service.url})
        return rows

    def update_bridge(self, **fields):
        if not self.bridge_settings.is_file():
            return
        current = json.loads(self.bridge_settings.read_text(encoding="utf-8-sig"))
        if any(current.get(key) != value for key, value in fields.items()):
            write_json(self.bridge_settings, current | fields)

    async def refresh_dsh_cookie(self):
        try:
            for _ in range(40):
                if 59934 in self.listeners():
                    break
                await asyncio.sleep(.5)
            else:
                return
            with impersonate(RUNNER, self.runner_password()) as token:
                log = Path(win32profile.GetUserProfileDirectory(token)) / "AppData/Local/FishHome/DSH/web-start.log"
                text = log.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"http://127\.0\.0\.1:59934/\?token=([A-Za-z0-9._~%-]+)", text)
            if not match:
                raise RuntimeError("DSH 启动日志没有连接票据")
            async with ClientSession(timeout=ClientTimeout(total=5)) as session:
                async with session.get("http://127.0.0.1:59934/connect", allow_redirects=False,
                        params={"token": unquote(match[1]), "interactions": "none"}) as response:
                    if response.status != 302 or len(response.cookies) != 1:
                        raise RuntimeError("DSH 连接票据无效")
                    name, value = next(iter(response.cookies.items()))
                    self.update_bridge(session_cookie=f"{name}={value.value}")
        except Exception as exc:
            with (self.state / "manager.log").open("a", encoding="utf-8") as log:
                log.write(f"DSH Bridge 票据刷新失败：{type(exc).__name__}\n")

    async def start(self, name):
        service = self.specs()[name]
        if name == "emulator":
            if self.emulator_status(service) != "stopped":
                return "已在运行或正在启动"
            return await self.emulator_action(service, "launch")
        if self.listeners().get(service.port) or self.tracked_pid(name) or self.launching_pid(service):
            return "已在运行或正在启动"
        argv = list(map(str, service.command))
        if not service.cwd.is_dir() or not Path(argv[0]).is_file():
            raise ValueError("组件安装目录或启动文件缺失")
        if name == "budget":
            self.activity.mkdir(parents=True, exist_ok=True)
            self.update_bridge(budget_token=self.creds.values["interaction"])
        if service.runner:
            password = self.runner_password()
            if not password:
                raise ValueError("请在 WebUI 保存 FishHomeRunner 密码后启动 DSH")
            pid = run_as(RUNNER, password, argv, env=service.env)
            with impersonate(RUNNER, password):
                started = psutil.Process(pid).create_time()
            task = asyncio.create_task(self.refresh_dsh_cookie())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        else:
            env = {key: val for key, val in os.environ.items()
                   if not key.startswith(("FISH_COST_", "FISH_MANAGER_", "FISHHOME_INTERACTION_TOKEN"))}
            with (self.state / f"{name}.log").open("ab") as log:
                child = subprocess.Popen(argv, cwd=service.cwd, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                    env=env | {"PYTHONUTF8": "1"} | service.env)
            pid, started = child.pid, psutil.Process(child.pid).create_time()
        self.launches[name] = {"pid": pid, "time": started}
        write_json(self.launch_file, self.launches)
        return "已启动，正在等待服务监听"

    async def stop(self, name):
        service = self.specs()[name]
        if name == "emulator":
            if self.emulator_status(service) == "stopped":
                return "已停止"
            return await self.emulator_action(service, "quit")
        pids = self.listeners().get(service.port, set())
        pid = self.tracked_pid(name)
        if service.runner and not pid and self.runner_password():
            with impersonate(RUNNER, self.runner_password()):
                pid = self.tracked_pid(name)
        if not pid and len(pids) == 1:
            candidate = next(iter(pids))
            pid = self.matching_pid(service, candidate)
        if not pid:
            if pids:
                raise ValueError("端口上有未确认归属的进程")
            return "已停止"
        argv = [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(pid), "/T", "/F"]
        if service.runner:
            if not self.runner_password():
                raise ValueError("需要 FishHomeRunner 凭据才能关闭 DSH")
            code = await asyncio.to_thread(run_as, RUNNER, self.runner_password(), argv, wait=True)
        else:
            child = await asyncio.create_subprocess_exec(*argv, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            code = await child.wait()
        if code and psutil.pid_exists(pid):
            raise RuntimeError("Windows 未能停止该进程")
        self.launches.pop(name, None)
        write_json(self.launch_file, self.launches)
        return "已请求停止"

    async def action(self, name, mode):
        async with self.lock:
            if mode == "start":
                return await self.start(name)
            if mode == "stop":
                return await self.stop(name)
            if mode == "restart":
                await self.stop(name)
                for _ in range(20):
                    service = self.specs()[name]
                    released = (self.emulator_status(service) == "stopped" if name == "emulator" else
                                not self.listeners().get(service.port))
                    if released:
                        return await self.start(name)
                    await asyncio.sleep(.25)
                raise RuntimeError("服务端口尚未释放")
            raise ValueError("未知操作")


def create_app(manager, token):
    @web.middleware
    async def local_only(request, handler):
        if request.host != f"127.0.0.1:{PORT}":
            return web.json_response({"error": "只允许本机访问"}, status=403)
        if request.path != "/" and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
        ):
            return web.json_response({"error": "请从系统托盘打开 WebUI"}, status=401)
        try:
            return await handler(request)
        except (ValueError, RuntimeError) as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (OSError, psutil.Error) as exc:
            return web.json_response({"error": f"Windows 操作失败（{type(exc).__name__}）"}, status=409)

    app = web.Application(middlewares=[local_only], client_max_size=8192)

    async def home(_request):
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html",
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"})

    async def status(_request):
        return web.json_response({"services": manager.statuses(), "official_key": bool(manager.creds.values.get("upstream_key")),
                                  "runner_password": bool(manager.runner_password())})

    async def ping(_request):
        return web.Response(text="FishHome")

    async def exit_console(_request):
        def shutdown():
            raise web.GracefulExit()
        asyncio.get_running_loop().call_later(.1, shutdown)
        return web.json_response({"message": "控制台退出"})

    async def action(request):
        name, mode = request.match_info["name"], request.match_info["mode"]
        if name == "all" and mode in {"start", "stop"}:
            order = list(manager.specs())
            outcomes = {}
            for item in order if mode == "start" else reversed(order):
                try:
                    outcomes[item] = await manager.action(item, mode)
                except (OSError, ValueError, RuntimeError, psutil.Error) as exc:
                    outcomes[item] = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            return web.json_response({"results": outcomes})
        if name not in manager.specs():
            raise ValueError("未知服务")
        return web.json_response({"message": await manager.action(name, mode)})

    async def configure(request):
        body = await request.json()
        if not isinstance(body, dict) or len(body) != 1:
            raise ValueError("一次只保存一项设置")
        name, value = next(iter(body.items()))
        if name == "upstream_key" and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{12,250}", value):
            manager.creds.values[name] = value
            manager.creds.save()
            return web.json_response({"message": "已加密保存 DeepSeek Key，请重启计费服务以启用。"})
        if name == "runner_password" and isinstance(value, str) and 0 < len(value) <= 256:
            with impersonate(RUNNER, value):
                pass
            manager.creds.values[name] = value
            manager.creds.save()
            return web.json_response({"message": "已验证并加密保存 DSH 账户密码。"})
        raise ValueError("配置格式不正确")

    async def setup_info(_request):
        return web.json_response({"interaction_token": manager.creds.values["interaction"],
                                  "autonomous_token": manager.creds.values["autonomous"]},
                                 headers={"Cache-Control": "no-store"})

    app.router.add_get("/", home)
    app.router.add_get("/api/ping", ping)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/services/{name}/{mode}", action)
    app.router.add_post("/api/config", configure)
    app.router.add_post("/api/exit", exit_console)
    app.router.add_get("/api/provider-keys", setup_info)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("FISH_MANAGER_TOKEN", "")
    if len(token) < 32:
        parser.error("Launch from FishHome.exe")
    web.run_app(create_app(Manager(args.dev.resolve(), args.state), token), host="127.0.0.1", port=PORT,
                access_log=None, print=None)


if __name__ == "__main__":
    main()
