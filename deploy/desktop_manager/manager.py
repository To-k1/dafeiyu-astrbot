"""Local WebUI and process supervision for FishHome's Windows tray app."""

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode
from uuid import uuid4

import psutil
import win32profile
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from deploy.bili_activity.ops import exclusive, save_control
from deploy.fish_cost_ledger import BUCKETS, Ledger, amount
from scripts.m6_data import export_memory
from scripts.native_archive import snapshot

from .windows import Credentials, impersonate, run_as, write_json


PORT = 6196
RUNNER = "FishHomeRunner"
ROOT = Path(__file__).resolve().parents[2]
PAGE = Path(__file__).with_name("index.html")
ICON = PAGE.with_name("fishhome.png")
INTERACTION_BASE = "http://127.0.0.1:6197/openai/interaction/v1"


def read_settings(path):
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        raise ValueError(f"{path.name} 不是有效的 JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象")
    return value


def qq_owner(config, owner):
    qq = [item for item in config.get("platform", [])
          if item.get("type") == "aiocqhttp" and item.get("enable") is True]
    if len(qq) != 1 or not qq[0].get("id") or qq[0].get("ws_reverse_port") != 6199:
        raise ValueError("请在 AstrBot 配置一个使用 6199 的 OneBot v11 反向连接")
    scope = f"{qq[0]['id']}:FriendMessage:{owner}"
    settings = config.get("platform_settings", {})
    whitelist = {str(item).strip() for item in settings.get("id_whitelist", []) if str(item).strip()}
    admins = [str(item).strip() for item in config.get("admins_id", [])]
    qq_admins = [item for item in admins if re.fullmatch(r"[0-9]+", item)]
    if (qq_admins != [owner]
            or settings.get("enable_id_white_list") is not True or whitelist != {scope}
            or settings.get("wl_ignore_admin_on_group") is not False
            or settings.get("wl_ignore_admin_on_friend") is not False):
        raise ValueError("在 AstrBot 保留唯一的主人私聊白名单，并关闭管理员群聊和私聊的白名单豁免")
    return scope


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
        self.options_path = state / "settings.json"
        self.options = read_settings(self.options_path)
        self.launch_file = state / "processes.json"
        self.launches = json.loads(self.launch_file.read_text(encoding="utf-8")) if self.launch_file.is_file() else {}
        self.astro = dev / "astrbot-4.29.4"
        self.python = self.astro / ".venv/Scripts/python.exe"
        self.data = self.astro / "data/plugin_data"
        self.bridge_settings = self.data / "astrbot_plugin_dsh_bridge/m3.json"
        self.activity = self.data / "astrbot_plugin_bili_activity"
        self.astrbot_settings = self.astro / "data/cmd_config.json"
        self.control = self.activity / "control.json"
        self.ledger = self.activity / "activity.sqlite3"
        self.bili_state = state / "bili"
        self.backups = dev.parent / "backups"
        self.exports = dev.parent / "exports"
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.archive_lock = asyncio.Lock()
        self.emulator_requested = 0.0

    def specs(self):
        cat, phone, ollama = (self.dev / name for name in ("napcat-4.18.38", "PhoneMCP", "ollama-0.40.15"))
        dsh = self.dev / "dsh-0.2.2-rc.1"
        emulator = Path(self.options.get("emulator_dir", r"C:\leidian\LDPlayer14"))
        bili = ROOT / "deploy/bili_activity/service.py"
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
                env={"OLLAMA_HOST": "127.0.0.1:11434",
                     "OLLAMA_MODELS": str(self.options.get("ollama_models", self.dev / "ollama-models")),
                     "OLLAMA_VULKAN": "1", "OLLAMA_NO_CLOUD": "1"}),
            "emulator": Service("雷电模拟器", emulator, (emulator / "ldconsole.exe",), 0, ""),
            "phone": Service("PhoneMCP", phone / "src", (phone / "venv/Scripts/python.exe", "main.py", "serve",
                "--transport", "sse", "--host", "127.0.0.1", "--port", "8009", "--path", "/Phone"), 8009,
                "main.py serve", env={"PATH": str(emulator) + ";" + os.environ["PATH"]}),
            "bili": Service("B 站伴侣（只读）", ROOT, (self.python, "-B", bili, "--state-dir", self.bili_state,
                "--adb-path", emulator / "adb.exe"), 8011, str(bili)),
            "dsh": Service("DeepSeek Harness", dsh, (cmd, "/d", "/c", "call", dsh / "Start-DSH-M3.cmd"),
                59934, str(dsh / "node_modules/@deepseek-ai/dsh/lib/bin.js"), env=dsh_env, runner=True),
            "astrbot": Service("AstrBot", self.astro, (self.python, "-B", "-m", "astrbot.cli", "run"), 6185,
                "astrbot.cli run", "http://127.0.0.1:6185/",
                env={"FISHHOME_INTERACTION_TOKEN": self.creds.values["interaction"]}),
            "napcat": Service("NapCat", cat, (cat / "node.exe", cat / "index.js", *cat_args), 6099,
                str(cat / "index.js"), "http://127.0.0.1:6099/webui/"),
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

    def configuration(self):
        specs = self.specs()
        bridge = read_settings(self.bridge_settings)
        control = read_settings(self.control)
        return {
            "napcat_qq": self.options.get("napcat_qq", ""), "owner_qq": str(bridge.get("owner_id", "")),
            "emulator_dir": str(specs["emulator"].cwd), "public_write": control.get("public_write_enabled") is True,
            "ollama_models": specs["ollama"].env["OLLAMA_MODELS"],
            "metered_dsh": self.options.get("metered_dsh") is True,
            "paths": {
                "AstrBot · QQ 白名单和模型": str(self.astrbot_settings),
                "LivingMemory · AstrBot 插件配置": str(self.astro / "data/config/astrbot_plugin_livingmemory_config.json"),
                "NapCat · OneBot 与登录": str(specs["napcat"].cwd / "napcat/config"),
                "DSH · 受控文件配置": str(specs["dsh"].cwd / "m5-file-only.yml"),
                "Ollama · 模型目录": specs["ollama"].env["OLLAMA_MODELS"],
                "PhoneMCP · 手机工具": str(specs["phone"].cwd),
                "B站 / 抖音 / 自主活动 · 模块配置": str(self.control),
                "B 站伴侣 · 本机状态": str(self.bili_state),
                "FishHome · 历史备份": str(self.backups),
                "FishHome · 记忆导出": str(self.exports),
                "FishHome · 启动配置与服务日志": str(self.state),
            },
        }

    def archive_history(self):
        rows = []
        for path in self.backups.glob("desktop-*.zip"):
            if not re.fullmatch(r"desktop-(?:backup|test-memory)-\d{8}T\d{6}(?:\.\d+)?Z\.zip", path.name):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            rows.append({"name": path.name, "bytes": info.st_size, "modified": info.st_mtime})
        return sorted(rows, key=lambda row: row["modified"], reverse=True)

    def create_archive(self, kind):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        if kind == "export":
            destination = self.exports / f"desktop-memory-export-{stamp}"
            export_memory(self.astro, destination)
            return destination.name
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                           timeout=5, creationflags=subprocess.CREATE_NO_WINDOW).strip()
        destination = self.backups / f"desktop-backup-{stamp}.zip"
        snapshot(self.astro, destination, environment="dev", revision=revision)
        return destination.name

    def set_config(self, name, value):
        if name in {"napcat_qq", "owner_qq"}:
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,11}", value):
                raise ValueError("请填写有效的 QQ 号码")
            if name == "owner_qq":
                if value == self.options.get("napcat_qq"):
                    raise ValueError("管理员 QQ 与机器人 QQ 应使用不同账户")
                scope = qq_owner(read_settings(self.astrbot_settings), value)
                self.bridge_settings.parent.mkdir(parents=True, exist_ok=True)
                write_json(self.bridge_settings, read_settings(self.bridge_settings) | {"owner_id": value, "owner_scope": scope})
                return "已保存 DSH Bridge 管理员。自检会检查自主活动的主人是否一致。"
            if value == str(read_settings(self.bridge_settings).get("owner_id", "")):
                raise ValueError("NapCat 快登请使用机器人 QQ 小号")
            message = "已保存机器人 QQ。重启 NapCat 后生效，登录验证在 NapCat 完成。"
        elif name == "emulator_dir":
            folder = Path(value) if isinstance(value, str) else Path()
            if not folder.is_absolute() or any(not (folder / exe).is_file() for exe in ("ldconsole.exe", "dnplayer.exe", "adb.exe")):
                raise ValueError("请选择包含 ldconsole.exe、dnplayer.exe 和 adb.exe 的雷电目录")
            value = str(folder.resolve())
            message = "已保存雷电目录。请关闭旧目录的模拟器，再启动雷电并重启 PhoneMCP。"
        elif name == "ollama_models":
            folder = Path(value) if isinstance(value, str) else Path()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("请选择已存在的 Ollama 模型目录")
            value = str(folder.resolve())
            message = "已保存模型目录。重启 Ollama 后生效。"
        elif name == "public_write" and type(value) is bool:
            if not self.control.is_file():
                raise ValueError("请先完成 B 站活动插件配置")
            if value and not self.activity_has_gate():
                raise ValueError("请先更新活动插件的公共写入接线")
            with exclusive(self.activity):
                control = read_settings(self.control)
                if (control.get("public_write_enabled") is True) != value:
                    save_control(self.activity, control | {"public_write_enabled": value})
            return "已更新公共写入总开关。各平台仍使用原有开关、审批和额度。"
        elif name == "metered_dsh" and value is True:
            if not self.dsh_profile_ready():
                raise ValueError("请先安装 DSH 的 FishHome 受控计费配置")
            message = "已保存 DSH 计费接线。请重启 DSH 后生效。"
        else:
            raise ValueError("配置格式不正确")
        updated = self.options | {name: value}
        write_json(self.options_path, updated)
        self.options = updated
        return message

    def dsh_profile_ready(self):
        folder = self.specs()["dsh"].cwd
        launcher = folder / "Start-DSH-M3.cmd"
        return ((folder / "m5-file-only.yml").is_file() and launcher.is_file()
                and "m5-file-only.yml" in launcher.read_text(encoding="utf-8-sig", errors="replace"))

    def activity_has_gate(self):
        plugin = self.astro / "data/plugins/astrbot_plugin_bili_activity"
        return all(path.is_file() and "public_write_enabled" in path.read_text(encoding="utf-8")
                   for path in (plugin / name for name in ("main.py", "interactions.py", "douyin.py")))

    def verify_qq(self):
        bridge = read_settings(self.bridge_settings)
        owner = str(bridge.get("owner_id", ""))
        if not owner:
            raise ValueError("请先设置 DSH Bridge 的管理员 QQ")
        if bridge.get("owner_scope") != qq_owner(read_settings(self.astrbot_settings), owner):
            raise ValueError("请重新保存管理员 QQ，以匹配 AstrBot 的 OneBot 实例名称")
        bot = str(self.options.get("napcat_qq", ""))
        if not bot or bot == owner:
            raise ValueError("请在控制台保存不同于管理员的机器人 QQ")
        return bridge

    def qq_ingress_ready(self):
        adapter = self.astro / (".venv/Lib/site-packages/astrbot/core/platform/sources/aiocqhttp/"
                                "aiocqhttp_platform_adapter.py")
        return adapter.is_file() and "FishHome: discard unrelated QQ events" in adapter.read_text(encoding="utf-8")

    async def diagnostics(self):
        checks = []

        def check(name, passed, ready, problem):
            checks.append({"name": name, "ok": passed, "detail": ready if passed else problem})

        specs = self.specs()
        for key, service in specs.items():
            launch_file = {"napcat": "index.js", "phone": "main.py", "dsh": "Start-DSH-M3.cmd",
                           "bili": "deploy/bili_activity/service.py"}.get(key)
            installed = service.cwd.is_dir() and service.command[0].is_file()
            if launch_file:
                installed = installed and (service.cwd / launch_file).is_file()
            if not installed:
                check(service.name, False, "", f"启动文件缺失，检查 {service.cwd}")

        config = read_settings(self.astrbot_settings)
        control = read_settings(self.control)
        bridge = read_settings(self.bridge_settings)
        try:
            self.verify_qq()
        except ValueError as exc:
            check("QQ 接收规则", False, "", str(exc))
        else:
            check("QQ 接收规则", True, "仅放行配置的管理员私聊", "")
        check("QQ 入口补丁", self.qq_ingress_ready(), "无关事件在转换与日志前拦截", "请重新应用本项目 OneBot 入口补丁")
        check("OneBot 连接", any(row.status == psutil.CONN_ESTABLISHED and row.laddr.port == 6199
                                 for row in psutil.net_connections("tcp") if row.laddr),
              "NapCat 已连 AstrBot", "请在 NapCat 核对登录及反向 WebSocket")
        check("活动主人", bool(bridge.get("owner_scope")) and control.get("owner_scope") == bridge["owner_scope"],
              "活动插件与 Bridge 的主人一致", "在活动插件配置内核对 owner_scope")
        providers = config.get("provider", [])
        flash = any(row.get("enable") is True and row.get("model") == "deepseek-flash"
                    and row.get("api_base", "").rstrip("/") == INTERACTION_BASE
                    and "${FISHHOME_INTERACTION_TOKEN}" in str(row.get("key", "")) for row in providers)
        check("交互计费配置", flash and bool(self.creds.values.get("upstream_key")),
              "Flash 指向本机交互账本，官方 Key 已保存", "核对 AstrBot Flash 提供商或保存官方 Key")
        lm_path = self.astro / "data/config/astrbot_plugin_livingmemory_config.json"
        memory = read_settings(lm_path).get("provider_settings", {})
        provider_ids = {row.get("id") for row in providers if row.get("enable") is True and row.get("id")}
        check("LivingMemory 配置", {memory.get("llm_provider_id"), memory.get("embedding_provider_id")} <= provider_ids,
              "总结和向量提供商已配置", "在 AstrBot 的 LivingMemory 插件中选择已启用的提供商")
        check("DSH 计费配置", bool(self.runner_password()) and self.options.get("metered_dsh") is True
              and self.dsh_profile_ready(), "专用账户与自主账本接线已配置", "检查 DSH 受控配置和账户凭据")
        paused = not any(control.get(key) for key in ("enabled", "periodic_enabled", "once_at", "public_write_enabled"))
        check("试跑范围", bool(control) and paused and self.activity_has_gate(), "自主活动、计划任务与公共写入关闭",
              "检查活动插件版本、自主活动、计划任务和公共写入")

        running = {row["id"] for row in self.statuses() if row["state"] == "running" and row["recognized"]}

        async def get(service, path, *, headers=None, body=None):
            if service not in running:
                return {}
            try:
                async with client.request("POST" if body else "GET", f"http://127.0.0.1:{specs[service].port}{path}",
                                          headers=headers, json=body, allow_redirects=False) as response:
                    if response.status == 200:
                        value = await response.json(content_type=None)
                        if isinstance(value, dict):
                            return value
            except (ClientError, OSError, TimeoutError, ValueError):
                pass
            return {}

        async def android():
            if "emulator" not in running:
                return False
            device = control.get("device_id") or "emulator-5554"
            child = await asyncio.create_subprocess_exec(str(specs["emulator"].cwd / "adb.exe"),
                "-s", device, "shell", "getprop", "sys.boot_completed", stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                out, _ = await asyncio.wait_for(child.communicate(), 3)
                return child.returncode == 0 and out.strip() == b"1"
            except TimeoutError:
                child.kill()
                await child.wait()
                return False

        async with ClientSession(timeout=ClientTimeout(total=4)) as client:
            budget, dsh, ollama, room, booted = await asyncio.gather(
                get("budget", "/balance", headers={"Authorization": "Bearer " + self.creds.values["interaction"]}),
                get("dsh", "/api/session/list", headers={"Cookie": bridge.get("session_cookie", "")}, body={
                    "type": "client-request", "rpcId": str(uuid4()), "method": "session/list",
                    "payload": {"args": {"_request": {}}}}),
                get("ollama", "/api/tags"), get("room", "/api/snapshot"), android())
        check("计费服务", "ledgers" in budget, "账本接口可读", "启动或重启人民币计费服务")
        check("DSH Bridge", dsh.get("result", {}).get("ok") is True, "已认证，能读取会话列表", "启动或重启 DSH 更新本机票据")
        embedder = next((row for row in providers if row.get("id") == memory.get("embedding_provider_id")), {})
        model = embedder.get("embedding_model", "")
        tags = {row.get("name") for row in ollama.get("models", [])}
        check("Ollama 模型", bool(model) and (model in tags or model + ":latest" in tags),
              f"本机已加载向量模型 {model}", "核对 Ollama 模型目录与 LivingMemory 配置的向量模型")
        check("房间与记忆星海", room.get("demo") is False and room.get("connection", {}).get("state") == "ready",
              "已读取真实 LivingMemory", "启动房间并查看 LivingMemory 连接状态")
        check("PhoneMCP 与雷电", "phone" in running and booted, "PhoneMCP 已监听，Android 已就绪",
              "启动 PhoneMCP 和雷电，等待 Android 完成开机")
        check("B 站伴侣", "bili" in running and control.get("bili_url", "").rstrip("/") == "http://127.0.0.1:8011/mcp",
              "本机只读服务已监听", "启动 B 站伴侣，在活动插件核对本机 MCP 地址")
        return checks

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

    def dsh_launch_token(self):
        password = self.runner_password()
        if not password:
            return ""
        with impersonate(RUNNER, password) as token:
            log = Path(win32profile.GetUserProfileDirectory(token)) / "AppData/Local/FishHome/DSH/web-start.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        tickets = re.findall(r"http://127\.0\.0\.1:59934/\?token=([A-Za-z0-9._~%-]+)", text)
        return unquote(tickets[-1]) if tickets else ""

    async def refresh_dsh_cookie(self):
        try:
            async with ClientSession(timeout=ClientTimeout(total=5)) as session:
                for _ in range(40):
                    if 59934 in self.listeners():
                        ticket = self.dsh_launch_token()
                        if ticket:
                            async with session.get("http://127.0.0.1:59934/connect", allow_redirects=False,
                                    params={"token": ticket, "interactions": "none"}) as response:
                                if response.status == 302 and len(response.cookies) == 1:
                                    name, value = next(iter(response.cookies.items()))
                                    self.update_bridge(session_cookie=f"{name}={value.value}")
                                    return
                    await asyncio.sleep(.5)
            raise RuntimeError("DSH 启动后未取得有效连接票据")
        except Exception as exc:
            with (self.state / "manager.log").open("a", encoding="utf-8") as log:
                log.write(f"DSH Bridge 票据刷新失败：{type(exc).__name__}\n")

    async def start(self, name):
        service = self.specs()[name]
        if name == "emulator":
            if self.emulator_status(service) != "stopped":
                return "已在运行或正在启动"
            return await self.emulator_action(service, "launch")
        pids = self.listeners().get(service.port, set())
        if pids:
            if not any(self.matching_pid(service, pid) for pid in pids):
                raise ValueError(f"端口 {service.port} 被未识别的进程占用")
            return "已在运行"
        if self.tracked_pid(name) or self.launching_pid(service):
            return "已在运行或正在启动"
        argv = list(map(str, service.command))
        if not service.cwd.is_dir() or not Path(argv[0]).is_file():
            raise ValueError("组件安装目录或启动文件缺失")
        if name == "napcat":
            self.verify_qq()
            if not self.qq_ingress_ready():
                raise ValueError("请先应用本项目 OneBot 入口补丁")
        if name == "bili" and not Path(argv[2]).is_file():
            raise ValueError("B 站伴侣启动文件缺失")
        if name == "budget":
            self.activity.mkdir(parents=True, exist_ok=True)
            self.update_bridge(budget_token=self.creds.values["interaction"])
        if service.runner:
            if self.options.get("metered_dsh") is not True or not self.dsh_profile_ready():
                raise ValueError("请先在控制台设置 DSH 计费接线")
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
        if request.path not in {"/", "/icon.png"} and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
        ):
            return web.json_response({"error": "请从系统托盘打开 WebUI"}, status=401)
        try:
            return await handler(request)
        except (ValueError, RuntimeError) as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (OSError, psutil.Error, sqlite3.Error, subprocess.SubprocessError) as exc:
            return web.json_response({"error": f"Windows 操作失败（{type(exc).__name__}）"}, status=409)

    app = web.Application(middlewares=[local_only], client_max_size=8192)

    async def home(_request):
        return web.Response(text=PAGE.read_text(encoding="utf-8"), content_type="text/html",
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"})

    async def icon(_request):
        return web.FileResponse(ICON)

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
        async with manager.lock:
            if name == "upstream_key" and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{12,250}", value):
                manager.creds.values[name] = value
                manager.creds.save()
                message = "已加密保存 DeepSeek Key，请重启计费服务以启用。"
            elif name == "runner_password" and isinstance(value, str) and 0 < len(value) <= 256:
                with impersonate(RUNNER, value):
                    pass
                manager.creds.values[name] = value
                manager.creds.save()
                message = "已验证并加密保存 DSH 账户密码。"
            else:
                message = manager.set_config(name, value)
        return web.json_response({"message": message})

    async def configuration(_request):
        return web.json_response(manager.configuration(), headers={"Cache-Control": "no-store"})

    async def diagnostics(_request):
        return web.json_response({"checks": await manager.diagnostics()}, headers={"Cache-Control": "no-store"})

    async def dsh_ui(_request):
        service = manager.specs()["dsh"]
        if not any(manager.matching_pid(service, pid) for pid in manager.listeners().get(service.port, ())):
            raise ValueError("请先启动 DeepSeek Harness")
        ticket = manager.dsh_launch_token()
        if not ticket:
            raise ValueError("DSH 登录票据尚未就绪，请重试或重启 DSH")
        return web.json_response({"url": "http://127.0.0.1:59934/?" + urlencode({"token": ticket})},
                                 headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def archive_history(_request):
        return web.json_response({"backups": await asyncio.to_thread(manager.archive_history)})

    async def archive(request):
        kind = request.match_info["kind"]
        if kind not in {"backup", "export"}:
            raise ValueError("未知归档操作")
        if manager.archive_lock.locked():
            raise ValueError("已有备份或导出正在执行")
        async with manager.archive_lock:
            name = await asyncio.to_thread(manager.create_archive, kind)
        return web.json_response({"name": name})

    async def location(request):
        body = await request.json()
        label = body.get("label") if isinstance(body, dict) else None
        path = manager.configuration()["paths"].get(label) if isinstance(label, str) else None
        if not path or not Path(path).exists():
            raise ValueError("配置位置不存在")
        path = Path(path)
        subprocess.Popen([str(Path(os.environ["SystemRoot"]) / "explorer.exe"), str(path if path.is_dir() else path.parent)])
        return web.json_response({"message": "已打开目录"})

    async def budgets(request):
        if not manager.ledger.is_file():
            raise ValueError("请先启动人民币计费服务建立账本")
        if request.method == "GET":
            data = await asyncio.to_thread(lambda: Ledger(manager.ledger).snapshot())
            return web.json_response(data, headers={"Cache-Control": "no-store"})
        body = await request.json()
        if (not isinstance(body, dict) or body.get("bucket") not in BUCKETS
                or type(body.get("temporary")) is not bool or not isinstance(body.get("amount"), str)):
            raise ValueError("请选择账本、金额和调额期限")
        amount(body["amount"])
        await asyncio.to_thread(lambda: Ledger(manager.ledger).adjust(body["bucket"], body["amount"], temporary=body["temporary"]))
        return web.json_response({"message": "已调整当日额度。" if body["temporary"] else "已调整默认额度。"})

    async def setup_info(_request):
        return web.json_response({"interaction_token": manager.creds.values["interaction"],
                                  "autonomous_token": manager.creds.values["autonomous"]},
                                 headers={"Cache-Control": "no-store"})

    app.router.add_get("/", home)
    app.router.add_get("/icon.png", icon)
    app.router.add_get("/api/ping", ping)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/services/{name}/{mode}", action)
    app.router.add_post("/api/config", configure)
    app.router.add_get("/api/config", configuration)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_post("/api/ui/dsh", dsh_ui)
    app.router.add_get("/api/archives", archive_history)
    app.router.add_post("/api/archives/{kind}", archive)
    app.router.add_post("/api/location", location)
    app.router.add_get("/api/budgets", budgets)
    app.router.add_post("/api/budgets", budgets)
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
