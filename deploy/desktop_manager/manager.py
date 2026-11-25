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
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode
from uuid import uuid4

import psutil
import pywintypes
import win32profile
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from plugin.astrbot_plugin_bili_activity import memewiki
from deploy.bili_activity.ops import DEFAULTS as ACTIVITY_DEFAULTS, NUMERIC_LIMITS, exclusive, ready, save_control, validate_dnd
from deploy.dsh_bridge.public_page import fetch_public_page, page_slice, save_public_page
from deploy.dsh_bridge.public_search import search_deepseek, validate_task_query
from deploy.dsh_bridge.go_profile import KEY_ENV, render_file_go_patch
from deploy.dsh_bridge.go_usage import read_go_key_usage, read_go_usage
from deploy.fish_cost_ledger import BUCKETS, UNCAPPED_SEARCH, Ledger, amount
from plugin.astrbot_plugin_bili_activity import dsh_cards
from plugin.astrbot_plugin_bili_activity.core import Store as ActivityStore
from plugin.astrbot_plugin_bili_activity import dsh_history_archive
from plugin.astrbot_plugin_bili_activity.dsh_roster import COPY_MEMORY_PREFIX, DshRoster
from plugin.astrbot_plugin_bili_activity.dsh_meetings import DshMeetings
from plugin.astrbot_plugin_bili_activity.team_mode import GROUP_SQL, configured_mode
from plugin.astrbot_plugin_bili_activity import shared_language
from scripts.m6_data import export_memory
from .backup_sync import archive_now, daily_backup, rotate as rotate_backups, sync as sync_backups

from .windows import Credentials, atomic_write, impersonate, run_as, write_json
from . import compute
from . import dashboard, desktop_session, host_resources, meeting_web, model_settings, owner_chat, owner_work, persona_mode, provider_mode, workspace_web
from .public_jobs import (PUBLIC_JOBS_PATH, WORKER_BODY_LIMIT, PublicJobs, PublicJobUnavailable,
                          report_excerpt, validate_agent, validate_public_job)
from .recovery import capture_and_pull
from .settings_sync import PEER_PATH, SettingsSync, SharedSettings, ThinkBookPeer
from .thinkbook import export_from_thinkbook, production_qq_marker, sync_room_from_thinkbook
from .video_worker import VideoWorker
from .video_transcript import TRANSCRIPT_MODES, media_folder, remove_media, run_transcript


PORT = 6196
VIDEO_PATH = "/api/worker/public-video"
SEARCH_PATH = "/api/worker/public-search"
DESKTOP_PATH = "/api/worker/windows-desktop"
GO_USAGE_PATH = "/api/worker/go-usage"
WORKER_SWITCHES = {VIDEO_PATH: "dsh_video_enabled", SEARCH_PATH: "dsh_web_search_enabled",
                   DESKTOP_PATH: "dsh_desktop_enabled"}
RUNNER = "FishHomeRunner"
ROOT = Path(__file__).resolve().parents[2]
DESKTOP_HOME = Path(r"D:\Share\code\FishHome")
PAGE = Path(__file__).with_name("index.html")
ICON = PAGE.with_name("fishhome.png")
INTERACTION_BASE = "http://127.0.0.1:6197/openai/interaction/v1"
ACTIVITY_NUMBERS = (
    "contact_daily_limit", "contact_unanswered_limit", "contact_cooldown_minutes", "contact_owner_quiet_minutes",
    "check_minutes", "check_jitter_minutes", "daily_minutes", "daily_model_calls", "max_model_calls_per_run",
    "daily_shares", "dsh_peak_token_limit",
    "project_active_limit", "project_max_stages", "project_review_delay_hours",
    "workshop_revisions_per_run", "workshop_max_characters",
)
ACTIVITY_SWITCHES = ("periodic_enabled", "dsh_agent_tasks_enabled", "autonomous_visual_enabled")
ACTIVITY_FIELDS = (*ACTIVITY_NUMBERS, *ACTIVITY_SWITCHES, "dnd_ranges")
DSH_SOURCE_FIELDS = ("commission_sources", "life_sources")
DSH_SOURCE_NAME = re.compile(r"[\w-][\w.-]{0,79}\.(?:csv|md|txt|json)", re.I)


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


def configured_dsh_sources(bridge, key):
    names = bridge.get(key)
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str) and DSH_SOURCE_NAME.fullmatch(name)]


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
    account: str = RUNNER


class Manager:
    def __init__(self, dev, state):
        self.dev, self.state = dev, state
        state.mkdir(parents=True, exist_ok=True)
        self.creds = Credentials(state / "credentials.bin")
        self.creds.values.setdefault(UNCAPPED_SEARCH, secrets.token_urlsafe(32))
        for bucket in ("interaction", "autonomous"):
            self.creds.values.setdefault(bucket, secrets.token_urlsafe(32))
            self.creds.values.setdefault("go_file_" + bucket, secrets.token_urlsafe(32))
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
        self.shared_settings = SharedSettings(self.control, self.ledger)
        self.settings_sync = (SettingsSync(self.shared_settings, state / "shared-settings-baseline.json", ThinkBookPeer())
                              if dev.parent == DESKTOP_HOME else None)
        self.bili_state = state / "bili"
        self.backups = dev.parent / "backups"
        self.exports = dev.parent / "exports"
        self.room_snapshot = state / "thinkbook-room.json" if dev.parent == DESKTOP_HOME else None
        self.room_sync_lock = asyncio.Lock()
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.archive_lock = asyncio.Lock()
        self.emulator_requested = 0.0
        self.video_worker = VideoWorker(self.dev / "dsh-0.2.2-rc.1", state, RUNNER)
        self._go_usage_lock = asyncio.Lock()
        self._go_usage_cache = None
        self.public_jobs = PublicJobs(state / "dsh-public-jobs", self.run_public_job,
                                     quota_available=self.public_go_available, transcript=self.run_transcript_job)
        self.owner_work = state / "owner-work"
        self.owner_jobs = PublicJobs(self.owner_work / "jobs", self.run_owner_job,
                                     quota_available=self.public_go_available, validate=owner_work.validate_job,
                                     transcript=self.run_transcript_job)

    def sync_video_worker(self):
        key = self.creds.values.get("opencode_key")
        token = ""
        if (self.room_snapshot is None and (any(self.options.get(key) is True for key in WORKER_SWITCHES.values())
                or self.options.get("dsh_go_files_enabled") is True)
                and key and self.runner_password() and self.bridge_settings.is_file()
                and (self.video_worker.dsh / "Start-DSH-Video.cmd").is_file()):
            token = self.creds.values.get("video_worker_token")
            if not token:
                token = secrets.token_urlsafe(32)
                self.creds.values["video_worker_token"] = token
                self.creds.save()
        self.update_bridge(video_worker_token=token, video_worker_enabled=bool(token) and
            self.options.get("dsh_video_enabled") is True, web_search_worker_enabled=bool(token) and
            self.options.get("dsh_web_search_enabled") is True, desktop_worker_enabled=bool(token) and
            self.options.get("dsh_desktop_enabled") is True and bool(self.creds.values.get("desktop_token")),
            go_usage_worker_enabled=bool(token), prefer_go_files=self.options.get("dsh_go_files_enabled") is True)
        return token

    def public_job_enabled(self, kind):
        switch = {"video": "dsh_video_enabled", "web": "dsh_web_search_enabled"}.get(kind)
        return bool(switch and self.options.get(switch) is True and self.sync_video_worker())

    def owner_job_enabled(self, kind):
        switch = {"video": "dsh_video_enabled", "web": "dsh_web_search_enabled", "desktop": "dsh_desktop_enabled"}.get(kind)
        return bool(self.room_snapshot is None and switch and self.options.get(switch) is True
                    and self.creds.values.get("opencode_key") and self.runner_password()
                    and (self.video_worker.dsh / "Start-DSH-Video.cmd").is_file()
                    and (kind != "desktop" or self.creds.values.get("desktop_token")))

    async def go_subscription_usage(self):
        async with self._go_usage_lock:
            key = self.creds.values.get("opencode_key")
            if not key:
                self._go_usage_cache = None
                return await read_go_usage(self.options.get("opencode_server_port", 28894),
                                           self.creds.values.get("opencode_server_password"))
            cache = self._go_usage_cache
            if not cache or cache[0] != key or time.monotonic() >= cache[1]:
                value = await read_go_key_usage(key)
                self._go_usage_cache = (key, time.monotonic() + 30, value)
            return self._go_usage_cache[2]

    async def public_go_available(self):
        return (await self.go_subscription_usage()).get("status") == "available"

    def archive_web_page(self, page):
        root = read_settings(self.bridge_settings).get("shared_workspace")
        if not root:
            raise ValueError("DSH 共享工作区未配置")
        with impersonate(RUNNER, self.runner_password()):
            return save_public_page(Path(root), page)

    async def search_web(self, query, bucket=UNCAPPED_SEARCH):
        return await search_deepseek(query, endpoint=f"http://127.0.0.1:6197/tools/{bucket}/search",
                                     token=self.creds.values[bucket])

    async def run_public_job(self, body, started, *, owner=False):
        kind = body["kind"]
        enabled = self.owner_job_enabled if owner else self.public_job_enabled
        if not enabled(kind):
            raise PublicJobUnavailable("DSH 公开资料工作进程不可用")
        password, go_key = self.runner_password(), self.creds.values["opencode_key"]
        language = shared_language.prompt(self.activity) if body.get("agent") else ""
        if kind == "web":
            archive = self.archive_web_page
            if owner:
                archive = lambda page: owner_work.archive_page(self.owner_work, page)
            return await self.video_worker.search(password, go_key, body["query"],
                                                  background_started=started, agent=body.get("agent"),
                                                  archive_page=archive, language=language, search=self.search_web,
                                                  owner_report=owner)
        mode = body.get("asr_mode", "auto")
        queue = self.owner_jobs.directory if owner else self.public_jobs.directory
        lease = body.get("preview_id") or getattr(started, "job_id", None)
        try:
            return await self.video_worker.run(password, go_key, body["reference"], body["question"],
                                               **self.video_audio_options(mode), background_started=started,
                                               agent=body.get("agent"), language=language,
                                               **({"media_cache": media_folder(queue, lease)} if lease else {}))
        finally:
            if lease and mode != "preview":
                remove_media(queue, lease)

    async def run_transcript_job(self, body, started, ident, should_pause, directory):
        mode = body["asr_mode"]
        audio = {}
        if mode != "ocr":
            options = self.video_audio_options(mode)
            audio = {"key": options["asr_key"], "model": options["asr_model"],
                     "workspace": options.get("asr_workspace", ""),
                     "language": body.get("asr_language", options.get("asr_language", "")),
                     "keep_dialect": options.get("keep_dialect", False)}
            model_settings.asr_endpoint(audio["model"], audio["workspace"])
        return await run_transcript(body, queue=directory, ident=ident, lease_id=body.get("preview_id") or ident,
                                    runtime=self.state / "media-runtime", audio_options=audio,
                                    progress=started, should_pause=should_pause)

    async def run_owner_job(self, body, started):
        if body["kind"] != "desktop":
            return await self.run_public_job(body, started, owner=True)
        if not self.owner_job_enabled("desktop"):
            raise PublicJobUnavailable("专用 Windows 桌面尚未启用或配对")
        pairing = self.creds.values["desktop_token"]
        return await self.video_worker.desktop(self.runner_password(), self.creds.values["opencode_key"],
                                               pairing, body["instruction"], background_started=started,
                                               owner_report=True)

    @property
    def compute_directory(self):
        return Path(os.environ["ProgramData"]) / "FishHomeComputeService"

    def service_password(self, service):
        if service.account == "FishHomeDesktop":
            return self.creds.values.get("desktop_password")
        return self.creds.values.get("compute_password") if service.account == compute.ACCOUNT else self.runner_password()

    def specs(self):
        cat, phone, ollama = (self.dev / name for name in ("napcat-4.18.38", "PhoneMCP", "ollama-0.40.15"))
        dsh = self.dev / "dsh-0.2.2-rc.1"
        emulator = Path(self.options.get("emulator_dir", r"C:\leidian\LDPlayer14"))
        bili = ROOT / "deploy/bili_activity/service.py"
        cmd = Path(os.environ["ComSpec"])
        qq = str(self.options.get("napcat_qq", ""))
        cat_args = ("-q", qq) if qq.isascii() and qq.isdigit() else ()
        room_source = (("--snapshot-file", self.room_snapshot) if self.room_snapshot is not None else
                       ("--astrbot-root", self.astro, "--life-data-dir", self.activity))
        key = self.creds.values.get("upstream_key")
        cost_env = {"FISH_COST_INTERACTION_TOKEN": self.creds.values["interaction"],
                    "FISH_COST_AUTONOMOUS_TOKEN": self.creds.values["autonomous"],
                    "FISH_COST_WEB_SEARCH_TOKEN": self.creds.values[UNCAPPED_SEARCH]}
        if key:
            cost_env["FISH_COST_UPSTREAM_KEY"] = key
        if self.options.get("dsh_go_files_enabled") is True and self.creds.values.get("opencode_key"):
            cost_env["FISH_GO_FILE_KEY"] = self.creds.values["opencode_key"]
            for bucket in ("interaction", "autonomous"):
                cost_env["FISH_GO_FILE_" + bucket.upper()] = self.creds.values["go_file_" + bucket]
        dsh_env = {}
        if self.options.get("metered_dsh") is True:
            dsh_env = {"DEEPSEEK_BASE_URL": "http://127.0.0.1:6197/anthropic/autonomous",
                       "FISHHOME_DSH_GATEWAY_TOKEN": self.creds.values["autonomous"],
                       "DEEPSEEK_API_KEY": self.creds.values["autonomous"]}
        commission = {}
        desktop_services = {}
        if self.room_snapshot is None and self.creds.values.get("desktop_password"):
            directory = Path(os.environ["ProgramData"]) / "FishHomeDesktop"
            account_profile = compute.profile("FishHomeDesktop")
            desktop_state = account_profile / "AppData/Local/FishHome/desktop"
            script = directory / "windows_desktop.py"
            desktop_services["desktop"] = Service("Windows 个人桌面", directory,
                (directory / "python/python.exe", "-B", script, "--workspace", desktop_state / "workspace",
                 "--token-file", desktop_state / "broker-token", "--init-token"),
                59937, str(script), runner=True, account="FishHomeDesktop")
        compute_services = {}
        if self.creds.values.get("compute_password"):
            directory = self.compute_directory
            script = directory / "compute_mcp.py"
            compute_services["compute"] = Service("Windows Python 执行器", directory,
                (directory / "python/python.exe", "-B", script, directory / "python.tar.gz", str(compute.PORT)),
                compute.PORT, str(script), runner=True, account=compute.ACCOUNT)
        if (dsh / "Start-DSH-Commission.cmd").is_file():
            interaction_env = {
                "DEEPSEEK_BASE_URL": "http://127.0.0.1:6197/anthropic/interaction",
                "FISHHOME_DSH_GATEWAY_TOKEN": self.creds.values["interaction"],
                "DEEPSEEK_API_KEY": self.creds.values["interaction"],
            }
            commission["dsh_commission"] = Service(
                "DeepSeek Harness（受托文件）", dsh, (cmd, "/d", "/c", "call", dsh / "Start-DSH-Commission.cmd"),
                59935, str(dsh / "m5-commission.yml"), env=interaction_env, runner=True)
        return {
            "budget": Service("人民币计费", ROOT, (self.python, "-B", "-m", "deploy.fish_cost", "--db",
                self.activity / "activity.sqlite3", "serve", "--request-bounds", *(() if key else ("--offline",))), 6197,
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
            **compute_services,
            **desktop_services,
            "dsh": Service("DeepSeek Harness", dsh, (cmd, "/d", "/c", "call", dsh / "Start-DSH-M3.cmd"),
                59934, str(dsh / "m5-file-only.yml"), env=dsh_env, runner=True),
            **commission,
            "astrbot": Service("AstrBot", self.astro, (self.python, "-B", "-m", "astrbot.cli", "run"), 6185,
                "astrbot.cli run", "http://127.0.0.1:6185/",
                env={"FISHHOME_INTERACTION_TOKEN": self.creds.values["interaction"],
                     "FISHHOME_AUTONOMOUS_TOKEN": self.creds.values["autonomous"],
                     **({provider_mode.GO_KEY_ENV: self.creds.values["opencode_key"]}
                        if self.creds.values.get("opencode_key") else {})}),
            "napcat": Service("NapCat", cat, (cat / "node.exe", cat / "index.js", *cat_args), 6099,
                str(cat / "index.js"), "http://127.0.0.1:6099/webui/"),
            "room": Service("房间与记忆星海", ROOT, (self.python, "-B", "-m", "deploy.memory_observatory.server",
                "--port", "8767", *room_source), 8767,
                "deploy.memory_observatory.server", "http://127.0.0.1:8767/room"),
        }

    async def sync_room(self):
        async with self.room_sync_lock:
            return await asyncio.to_thread(sync_room_from_thinkbook, self.room_snapshot)

    @staticmethod
    def listeners():
        listeners = {}
        for entry in psutil.net_connections("tcp"):
            if entry.status == psutil.CONN_LISTEN and entry.laddr:
                listeners.setdefault(entry.laddr.port, set()).add(entry.pid)
        return listeners

    def runner_password(self):
        return self.creds.values.get("runner_password")

    def dsh_asr_options(self):
        if self.options.get("dsh_asr_free_only_confirmed") is not True:
            return {}
        face = read_settings(self.astro / model_settings.FACE_PATH)
        key = model_settings.asr_key(face, read_settings(self.astrbot_settings))
        return {"asr_key": key, "asr_model": face.get("dsh_asr_model") or model_settings.ASR_MODEL,
                **({"asr_workspace": face["asr_workspace"]} if face.get("asr_workspace") else {}),
                **({"asr_language": face["dsh_asr_language"]} if face.get("dsh_asr_language") else {}),
                **({"keep_dialect": True} if face.get("asr_keep_dialect") is True else {})} if key else {}

    def video_audio_options(self, mode):
        options = {} if mode in ("off", "preview") else self.dsh_asr_options()
        if mode in ("audio", "both", "ocr_audio") and not options.get("asr_key"):
            raise PublicJobUnavailable("DSH 原音听写未启用或缺少百炼 Key，请先检查语音设置。")
        if mode != "auto":
            options["asr_mode"] = mode
        return options

    def configuration(self):
        specs = self.specs()
        bridge = read_settings(self.bridge_settings)
        control = read_settings(self.control)
        return {
            "napcat_qq": self.options.get("napcat_qq", ""), "owner_qq": str(bridge.get("owner_id", "")),
            "emulator_dir": str(specs["emulator"].cwd), "public_write": control.get("public_write_enabled") is True,
            "ollama_models": specs["ollama"].env["OLLAMA_MODELS"],
            "metered_dsh": self.options.get("metered_dsh") is True,
            "compute_enabled": self.options.get("compute_enabled") is True,
            "opencode_key_saved": bool(self.creds.values.get("opencode_key")),
            "dsh_video_supported": self.room_snapshot is None and
                (self.video_worker.dsh / "Start-DSH-Video.cmd").is_file(),
            "dsh_video_enabled": self.options.get("dsh_video_enabled") is True and
                bool(bridge.get("video_worker_token")),
            "dsh_web_search_enabled": self.options.get("dsh_web_search_enabled") is True and
                bool(bridge.get("video_worker_token")),
            "dsh_desktop_enabled": bridge.get("desktop_worker_enabled") is True,
            "dsh_desktop_paired": bool(self.creds.values.get("desktop_token")),
            "desktop_password_saved": bool(self.creds.values.get("desktop_password")),
            "desktop_auto_enabled": self.options.get("desktop_auto_enabled") is True,
            "dsh_go_files_enabled": self.options.get("dsh_go_files_enabled") is True,
            "dsh_go_file_routes": {kind: bool(bridge.get(kind + "_go_file_token")) for kind in ("life", "commission")},
            "go_usage_paired": bool(self.creds.values.get("opencode_server_password")),
            "dsh_asr_free_only_confirmed": self.options.get("dsh_asr_free_only_confirmed") is True,
            "settings_sync_supported": self.settings_sync is not None,
            "activity_settings": ({key: control.get(key, ACTIVITY_DEFAULTS[key]) for key in ACTIVITY_FIELDS}
                                  if self.control.is_file() else None),
            "activity_limits": {key: NUMERIC_LIMITS.get(key, (1, None)) for key in ACTIVITY_NUMBERS},
            "contact_coordinated": control.get("contact_coordination_enabled") is True,
            "dsh_sources": ({key: configured_dsh_sources(bridge, key) for key in DSH_SOURCE_FIELDS}
                            if self.bridge_settings.is_file() else None),
            "dsh_shared_workspace": bridge.get("shared_workspace") or None,
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
        for path in self.backups.glob("*.zip"):
            if not re.fullmatch(r"(?:desktop-(?:backup|test-memory)|thinkbook-(?:recovery|backup(?:-daily)?))-\d{8}T\d{6}(?:\.\d+)?Z\.zip", path.name):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            rows.append({"name": path.name, "bytes": info.st_size, "modified": info.st_mtime})
        return sorted(rows, key=lambda row: row["modified"], reverse=True)

    def create_archive(self, kind):
        if kind == "recovery":
            return capture_and_pull(self.backups).name
        if kind == "sync":
            name = sync_backups(self.backups)
            if not name:
                raise ValueError("ThinkBook 还没有普通备份可同步")
            rotate_backups(self.backups)
            return name
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        if kind == "export-thinkbook":
            return export_from_thinkbook(self.exports / f"thinkbook-memory-export-{stamp}").name
        if kind == "export":
            destination = self.exports / f"desktop-memory-export-{stamp}"
            export_memory(self.astro, destination)
            return destination.name
        revision = None
        if (ROOT / ".git").exists():
            try:
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                                   stderr=subprocess.DEVNULL, timeout=5,
                                                   creationflags=subprocess.CREATE_NO_WINDOW).strip()
            except (OSError, subprocess.SubprocessError):
                pass
        return archive_now(self.astro, self.backups, desktop=self.dev.parent == DESKTOP_HOME, revision=revision).name

    def set_config(self, name, value):
        if name in ACTIVITY_FIELDS:
            return self.set_activity_config(name, value)
        if name in DSH_SOURCE_FIELDS:
            if not self.bridge_settings.is_file():
                raise ValueError("请先完成 DSH Bridge 配置")
            if not isinstance(value, list) or any(not isinstance(item, str) or not DSH_SOURCE_NAME.fullmatch(item)
                                                  for item in value):
                raise ValueError("请输入工作区中的 CSV、Markdown、TXT 或 JSON 文件名，不能填写目录")
            self.update_bridge(**{name: list(dict.fromkeys(value))})
            return "已保存素材名单。请确认文件已放入相应的 DSH 工作目录。"
        if name in {"napcat_qq", "owner_qq"}:
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,11}", value):
                raise ValueError("请填写有效的 QQ 号码")
            if name == "owner_qq":
                if value == self.options.get("napcat_qq"):
                    raise ValueError("管理员 QQ 与机器人 QQ 应使用不同账户")
                scope = qq_owner(read_settings(self.astrbot_settings), value)
                self.bridge_settings.parent.mkdir(parents=True, exist_ok=True)
                write_json(self.bridge_settings, read_settings(self.bridge_settings) | {
                    "owner_id": value, "owner_scope": scope, "budget_token": self.creds.values["interaction"],
                })
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

    def set_activity_config(self, name, value):
        if not self.control.is_file():
            raise ValueError("请先完成活动插件配置")
        if name in ACTIVITY_NUMBERS:
            low, high = NUMERIC_LIMITS.get(name, (1, None))
            if type(value) is not int or value < low or (high is not None and value > high):
                constraint = f"{low}–{high}" if high is not None else f"至少 {low}"
                raise ValueError(f"请输入{constraint}的整数")
        elif name in ACTIVITY_SWITCHES:
            if type(value) is not bool:
                raise ValueError("请选择开启或关闭")
        elif name == "dnd_ranges":
            validate_dnd(value)
        else:
            raise ValueError("未知的活动设置")

        with exclusive(self.activity):
            control = read_settings(self.control)
            update = {name: value}
            if name == "periodic_enabled" and value:
                ready(ACTIVITY_DEFAULTS | control)
                if control.get("one_shot_verified") is not True:
                    raise ValueError("先完成活动验收，再开启周期调度")
                update["enabled"] = True
            if name == "dsh_agent_tasks_enabled" and value and control.get("life_enabled") is not True:
                raise ValueError("先启用持续生活功能，再允许自主派发 DSH 任务")
            if any(control.get(key, ACTIVITY_DEFAULTS[key]) != val for key, val in update.items()):
                save_control(self.activity, control | update)
        return "已保存，活动插件将在下一次读取配置时生效。"

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
            if service.runner and self.service_password(service):
                try:
                    with impersonate(service.account, self.service_password(service)):
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

    def dsh_launch_token(self, port=59934):
        password = self.runner_password()
        if not password:
            return ""
        with impersonate(RUNNER, password) as token:
            state = "DSH-Commission" if port == 59935 else "DSH"
            log = Path(win32profile.GetUserProfileDirectory(token)) / "AppData/Local/FishHome" / state / "web-start.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        tickets = re.findall(rf"http://127\.0\.0\.1:{port}/\?token=([A-Za-z0-9._~%-]+)", text)
        return unquote(tickets[-1]) if tickets else ""

    async def refresh_dsh_cookie(self, port=59934, *, compute_enabled=None, file_go_token=None):
        try:
            async with ClientSession(timeout=ClientTimeout(total=5)) as session:
                deadline = asyncio.get_running_loop().time() + 90
                while asyncio.get_running_loop().time() < deadline:
                    if port in self.listeners():
                        ticket = self.dsh_launch_token(port)
                        if ticket:
                            try:
                                async with session.get(f"http://127.0.0.1:{port}/connect", allow_redirects=False,
                                        params={"token": ticket, "interactions": "none"}) as response:
                                    if response.status == 302 and len(response.cookies) == 1:
                                        name, value = next(iter(response.cookies.items()))
                                        field = "commission_cookie" if port == 59935 else "session_cookie"
                                        fields = {field: f"{name}={value.value}"}
                                        if compute_enabled is not None:
                                            route = "commission_compute_enabled" if port == 59935 else "life_compute_enabled"
                                            fields[route] = compute_enabled and self.options.get("compute_enabled") is True
                                        if file_go_token is not None:
                                            route = "commission" if port == 59935 else "life"
                                            fields[route + "_go_file_token"] = file_go_token
                                        self.update_bridge(**fields)
                                        return
                            except (ClientError, OSError, asyncio.TimeoutError):
                                pass
                    await asyncio.sleep(.5)
            raise RuntimeError("DSH 启动后未取得有效连接票据")
        except Exception as exc:
            with (self.state / "manager.log").open("a", encoding="utf-8") as log:
                log.write(f"DSH Bridge 票据刷新失败：{type(exc).__name__}\n")

    async def start(self, name):
        service = self.specs()[name]
        is_dsh = name in {"dsh", "dsh_commission"}
        use_compute = self.options.get("compute_enabled") is True
        use_file_go = self.options.get("dsh_go_files_enabled") is True and is_dsh
        if name == "compute" and not use_compute:
            return "计算执行已关闭"
        if name == "napcat" and self.room_snapshot is not None and production_qq_marker().is_file():
            raise ValueError("ThinkBook 承载正式 QQ，台式机 NapCat 保持停止。")
        if name == "emulator":
            if self.emulator_status(service) != "stopped":
                return "已在运行或正在启动"
            return await self.emulator_action(service, "launch")
        pids = self.listeners().get(service.port, set())
        if pids:
            recognized = [pid for pid in pids if self.matching_pid(service, pid)]
            if not recognized:
                raise ValueError(f"端口 {service.port} 被未识别的进程占用")
            if name == "desktop":
                current_session = desktop_session.get_session()["session_id"]
                with impersonate(service.account, self.service_password(service)):
                    other_session = any(desktop_session.session_id(pid) != current_session for pid in recognized)
                if other_session:
                    raise ValueError("旧桌面服务在另一 Windows 会话，请先切换到该会话关闭服务")
            if name == "budget":
                self.update_bridge(budget_token=self.creds.values["interaction"])
            if is_dsh and self.runner_password() and self.bridge_settings.is_file():
                task = asyncio.create_task(self.refresh_dsh_cookie(service.port))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            return "已在运行"
        if self.tracked_pid(name) or self.launching_pid(service):
            return "已在运行或正在启动"
        if name == "room" and self.room_snapshot is not None and not self.room_snapshot.is_file():
            await self.sync_room()
        argv = list(map(str, service.command))
        if name == "compute":
            token = self.creds.values.setdefault("compute_token", secrets.token_urlsafe(32))
            self.creds.save()
            configured_root = read_settings(self.bridge_settings).get("dsh_workspaces_root")
            projects = Path(configured_root) / "dsh" / "projects" if configured_root else None
            await asyncio.to_thread(compute.prepare, self.compute_directory, ROOT / "deploy/dsh_bridge",
                self.dev.parent / "downloads/python-3.12.10-windows-x64.tar.gz", self.python, token, RUNNER, projects)
        if name == "desktop":
            with impersonate(service.account, self.service_password(service)):
                available = service.cwd.is_dir() and Path(argv[0]).is_file() and Path(argv[2]).is_file()
        else:
            available = service.cwd.is_dir() and Path(argv[0]).is_file()
        if not available:
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
        if name == "desktop":
            pid = desktop_session.launch_desktop(service.account, self.service_password(service), argv, {})
            with impersonate(service.account, self.service_password(service)):
                started = psutil.Process(pid).create_time()
        elif name == "compute":
            profile = compute.profile(compute.ACCOUNT)
            pid = run_as(compute.ACCOUNT, self.service_password(service), argv, env={
                "PYTHONUTF8": "1", "USERPROFILE": str(profile), "LOCALAPPDATA": str(profile / "AppData/Local"),
                "FISHHOME_COMPUTE_TOKEN": token,
                **({"FISHHOME_COMPUTE_PROJECTS": str(projects)} if projects is not None else {}),
            })
            with impersonate(compute.ACCOUNT, self.service_password(service)):
                started = psutil.Process(pid).create_time()
        elif is_dsh:
            if (not use_file_go and self.options.get("metered_dsh") is not True) or not self.dsh_profile_ready():
                raise ValueError("请先在控制台设置 DSH 计费接线")
            password = self.runner_password()
            if not password:
                raise ValueError("请在 WebUI 保存 FishHomeRunner 密码后启动 DSH")
            configured = read_settings(self.bridge_settings)
            public_root = configured.get("dsh_workspaces_root")
            public_workspace = None
            if public_root:
                root = Path(public_root)
                if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
                    raise ValueError("DSH 公共工作区无效")
                public_workspace = root / "dsh" / ("commission" if name == "dsh_commission" else "life")
                if not public_workspace.is_dir() or public_workspace.resolve() != public_workspace:
                    raise ValueError("DSH 项目目录无效")
            if name == "dsh_commission":
                if not service.cwd.joinpath("m5-commission.yml").is_file():
                    raise ValueError("受托文件执行配置缺失")
                with impersonate(RUNNER, password) as token:
                    root = public_workspace or (Path(win32profile.GetUserProfileDirectory(token))
                                                / "AppData/Local/FishHome/commission-files")
                    root.mkdir(parents=True, exist_ok=True)
                    (root / "output").mkdir(exist_ok=True)
                self.update_bridge(commission_workspace=str(root), commission_cookie="")
            elif public_workspace:
                self.update_bridge(life_workspace=str(public_workspace), session_cookie="")
            extra_patches = []
            if use_compute:
                if "compute" not in self.specs():
                    raise ValueError("请先保存 FishHomeCompute 的密码")
                await self.start("compute")
                async with ClientSession(timeout=ClientTimeout(total=2)) as client:
                    for _ in range(20):
                        try:
                            async with client.post(f"http://127.0.0.1:{compute.PORT}/mcp", json={
                                "jsonrpc": "2.0", "id": 1, "method": "ping",
                            }, headers={"Authorization": "Bearer " + self.creds.values["compute_token"]}) as response:
                                if response.status == 200:
                                    break
                        except (ClientError, TimeoutError):
                            pass
                        await asyncio.sleep(.5)
                    else:
                        raise ValueError("Windows Python 执行器没有就绪")
                extra_patches.append(self.compute_directory / "compute.yml")
            env = service.env
            go_token = ""
            if use_file_go:
                if not self.creds.values.get("opencode_key"):
                    raise ValueError("请先保存 OpenCode Go Key")
                bucket = "interaction" if name == "dsh_commission" else "autonomous"
                go_token = self.creds.values["go_file_" + bucket]
                async with ClientSession(timeout=ClientTimeout(total=3), trust_env=False) as client:
                    async with client.get(f"http://127.0.0.1:6197/go/{bucket}/receipts/readiness",
                            headers={"Authorization": "Bearer " + go_token}) as response:
                        if response.status != 200:
                            raise ValueError("请先重启人民币计费服务以装载独立 Go 网关")
                session_plugin = service.cwd / "go-session-header.mjs"
                if not session_plugin.is_file():
                    raise ValueError("DSH Go 会话插件缺失")
                with impersonate(RUNNER, password) as token:
                    folder = Path(win32profile.GetUserProfileDirectory(token)) / "AppData/Local/FishHome"
                    patch = folder / f"go-files-{bucket}.json"
                    write_json(patch, render_file_go_patch(bucket, session_plugin))
                extra_patches.append(patch)
                env = {KEY_ENV: go_token}
            meeting_plugin = service.cwd / "fishhome-meetings/index.mjs"
            if meeting_plugin.is_file():
                with impersonate(RUNNER, password) as token:
                    folder = Path(win32profile.GetUserProfileDirectory(token)) / "AppData/Local/FishHome"
                    patch = folder / "fishhome-meetings.json"
                    write_json(patch, [{"insert": [{"id":"fishhome-dsh-meetings", "name":str(meeting_plugin)}]}])
                extra_patches.append(patch)
            if public_workspace:
                env = env | {"FISHHOME_DSH_WORKSPACE": str(public_workspace)}
            if extra_patches or public_workspace:
                launcher = service.cwd / "Start-DSH-Compute.cmd"
                if not launcher.is_file():
                    raise ValueError("DSH 专用启动脚本缺失")
                argv = [*argv[:4], str(launcher), str(service.port), *map(str, extra_patches)]
            pid = run_as(RUNNER, password, argv, env=env)
            with impersonate(RUNNER, password):
                started = psutil.Process(pid).create_time()
            task = asyncio.create_task(self.refresh_dsh_cookie(service.port, compute_enabled=use_compute, file_go_token=go_token))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        else:
            env = {key: val for key, val in os.environ.items()
                   if not key.startswith(("FISH_COST_", "FISH_GO_FILE_", "FISH_MANAGER_", "FISHHOME_INTERACTION_TOKEN",
                                          "FISHHOME_AUTONOMOUS_TOKEN"))}
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
        if name == "desktop":
            listeners = self.listeners().get(service.port, set())
            password = self.service_password(service)
            with impersonate(service.account, password):
                if listeners:
                    candidates = [pid for pid in listeners if self.matching_pid(service, pid)]
                    if len(candidates) != 1:
                        raise ValueError("桌面服务端口被未识别的进程占用")
                    pid = candidates[0]
                else:
                    pid = self.tracked_pid(name) or self.launching_pid(service)
                try:
                    started = psutil.Process(pid).create_time() if pid else None
                except psutil.NoSuchProcess:
                    pid = None
            if listeners:
                if not pid:
                    raise ValueError("桌面服务进程已退出，请重试")
                pairing = self.creds.values.get("desktop_token")
                if not pairing:
                    raise ValueError("请先配对桌面服务")
                async with ClientSession(timeout=ClientTimeout(total=8), trust_env=False) as session:
                    async with session.post("http://127.0.0.1:59937/desktop",
                            json={"op": "shutdown"}, headers={"Authorization": "Bearer " + pairing}) as response:
                        result = await response.json()
                        if response.status != 200:
                            raise ValueError(result.get("error", "桌面服务未确认停止"))
            if pid:
                def wait_for_exit():
                    with impersonate(service.account, password):
                        try:
                            process = psutil.Process(pid)
                            if process.create_time() == started:
                                process.wait(timeout=8)
                        except psutil.NoSuchProcess:
                            pass
                        except psutil.TimeoutExpired as exc:
                            raise ValueError("桌面服务尚未退出，请稍后重试") from exc
                await asyncio.to_thread(wait_for_exit)
            self.launches.pop(name, None)
            write_json(self.launch_file, self.launches)
            return "桌面服务已停止" if pid else "已停止"
        if name == "compute":
            self.update_bridge(life_compute_enabled=False, commission_compute_enabled=False)
        elif name in {"dsh", "dsh_commission"}:
            field = "commission_compute_enabled" if name == "dsh_commission" else "life_compute_enabled"
            prefix = "commission" if name == "dsh_commission" else "life"
            self.update_bridge(**{field: False, prefix + "_go_file_token": ""})
        if name == "emulator":
            if self.emulator_status(service) == "stopped":
                return "已停止"
            return await self.emulator_action(service, "quit")
        pids = self.listeners().get(service.port, set())
        pid = self.tracked_pid(name)
        if service.runner and not pid and self.service_password(service):
            with impersonate(service.account, self.service_password(service)):
                pid = self.tracked_pid(name)
        if not pid and len(pids) == 1:
            candidate = next(iter(pids))
            pid = self.matching_pid(service, candidate)
        if not pid:
            if pids:
                raise ValueError("端口上有未确认归属的进程")
            if name == "dsh_commission":
                self.update_bridge(commission_cookie="")
            return "已停止"
        argv = [str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(pid), "/T", "/F"]
        if service.runner:
            if not self.service_password(service):
                raise ValueError(f"需要 {service.account} 凭据才能关闭服务")
            code = await asyncio.to_thread(run_as, service.account, self.service_password(service), argv, wait=True)
        else:
            child = await asyncio.create_subprocess_exec(*argv, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            code = await child.wait()
        if code and psutil.pid_exists(pid):
            raise RuntimeError("Windows 未能停止该进程")
        if name == "dsh_commission":
            self.update_bridge(commission_cookie="")
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
        if request.path.startswith("/api/worker/public-jobs/"):
            worker_token = manager.creds.values.get("video_worker_token")
            if not worker_token or not hmac.compare_digest(request.headers.get("Authorization", "").encode(),
                                                              f"Bearer {worker_token}".encode()):
                return web.json_response({"error": "DSH 公开资料工作进程不可用"}, status=401)
        elif request.path in WORKER_SWITCHES or request.path == GO_USAGE_PATH:
            worker_token = manager.sync_video_worker()
            if (not worker_token or (request.path in WORKER_SWITCHES
                    and manager.options.get(WORKER_SWITCHES[request.path]) is not True)
                    or not hmac.compare_digest(request.headers.get("Authorization", "").encode(),
                                               f"Bearer {worker_token}".encode())):
                return web.json_response({"error": "DSH 公开资料工作进程不可用"}, status=401)
        elif request.path not in {"/", "/classic", "/icon.png", meeting_web.PAGE, owner_work.PAGE, owner_work.CHAT_PAGE} and not (
            hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode())
            or meeting_web.authorize(request, token, PORT)
            or owner_work.authorize(request, token, PORT)
        ):
            return web.json_response({"error": "请从系统托盘打开 WebUI"}, status=401)
        try:
            return await handler(request)
        except (ValueError, RuntimeError) as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (OSError, psutil.Error, pywintypes.error, sqlite3.Error, subprocess.SubprocessError) as exc:
            return web.json_response({"error": f"Windows 操作失败（{type(exc).__name__}）"}, status=409)

    app = web.Application(middlewares=[local_only], client_max_size=8192)
    meme_update_lock = asyncio.Lock()

    async def meme_dictionary(request):
        if request.method == "GET":
            return web.json_response(await asyncio.to_thread(memewiki.status, manager.activity))
        if meme_update_lock.locked():
            raise ValueError("词库正在更新")
        async with meme_update_lock:
            result = await asyncio.to_thread(memewiki.update, manager.activity)
        return web.json_response(result)

    async def configure_video(_app):
        manager.sync_video_worker()

    app.on_startup.append(configure_video)

    async def start_desktop(_app):
        if manager.options.get("desktop_auto_enabled") is True and manager.creds.values.get("desktop_password"):
            try:
                async with manager.lock:
                    await manager.start("desktop")
            except (OSError, ValueError, RuntimeError, KeyError, psutil.Error, pywintypes.error) as exc:
                with (manager.state / "manager.log").open("a", encoding="utf-8") as log:
                    log.write(f"桌面服务自动启动失败：{type(exc).__name__}\n")

    app.on_startup.append(start_desktop)

    async def public_job_worker(_app):
        manager.public_jobs.start()
        owner_jobs = getattr(manager, "owner_jobs", None)
        if owner_jobs is not None:
            owner_jobs.start()
        maintenance = [asyncio.create_task(jobs.maintain_media()) for jobs in
                       (manager.public_jobs, owner_jobs) if jobs is not None]
        try:
            yield
        finally:
            for task in maintenance:
                task.cancel()
            await asyncio.gather(*maintenance, return_exceptions=True)
            await manager.public_jobs.close()
            if owner_jobs is not None:
                await owner_jobs.close()

    app.cleanup_ctx.append(public_job_worker)

    async def public_job(request):
        ident = request.match_info["ident"]
        if request.method == "GET":
            status = manager.public_jobs.get(ident)
        elif request.method == "DELETE":
            status = manager.public_jobs.cancel(ident)
        else:
            body = await request.clone(client_max_size=WORKER_BODY_LIMIT).json()
            if isinstance(body, dict) and body.get("control") in ("pause", "resume") and body.keys() == {"control"}:
                status = manager.public_jobs.control(ident, body["control"])
            else:
                local_video = isinstance(body, dict) and body.get("asr_mode") in TRANSCRIPT_MODES
                status = manager.public_jobs.submit(ident, body, enabled=lambda kind: (
                    local_video and kind == "video" or manager.public_job_enabled(kind)))
        if report := status.get("report"):
            if request.method == "GET" and request.query.get("report") == "full":
                if full := manager.public_jobs.full_report(ident, report):
                    status["report"] = {**report, "full_report": full}
            else:
                status["report"] = report_excerpt(report)
        return web.json_response(status, headers={"Cache-Control": "no-store"})

    async def delegate_video(request):
        body = await request.json()
        if not isinstance(body, dict) or body.keys() - {"asr_mode", "report_detail"} != {"reference", "question"}:
            raise ValueError("代看只接受公开视频链接和问题")
        detail = body.pop("report_detail", "brief")
        if detail not in ("brief", "full"):
            raise ValueError("请选择简报或完整报告")
        spec = validate_public_job({**body, "kind": "video"})
        mode = spec.get("asr_mode", "auto")
        lease = uuid4().hex
        queue = manager.public_jobs.directory
        try:
            report = await manager.video_worker.run(manager.runner_password(), manager.creds.values["opencode_key"],
                                                    body["reference"], body["question"],
                                                    **manager.video_audio_options(mode),
                                                    media_cache=media_folder(queue, lease))
        except (ClientError, TimeoutError, KeyError, TypeError):
            raise RuntimeError("DSH 未返回可核实的代看结果") from None
        finally:
            if mode != "preview":
                remove_media(queue, lease)
        if mode == "preview" and report.get("preview"):
            report = {**report, "task_id": lease}
        if report.get("full_report"):
            manager.public_jobs.archive_video(report["task_id"], spec, report)
            report = {**report, "has_full_report": True}
        return web.json_response(report if detail == "full" else report_excerpt(report),
                                 headers={"Cache-Control": "no-store"})

    async def delegate_search(request):
        body = await request.json()
        if (not isinstance(body, dict) or body.keys() - {"bucket"} != {"query", "mode"}
                or body["mode"] not in ("default", "personal", "page")
                or ("bucket" in body and (body["mode"] != "personal" or body["bucket"] not in BUCKETS))):
            raise ValueError("只接受公开搜索问题和搜索方式")
        try:
            if body["mode"] == "personal":
                rows = await manager.search_web(body["query"], body.get("bucket", UNCAPPED_SEARCH))
                report = {"kind": "personal_web_search", "results": rows}
            elif body["mode"] == "page":
                page = await fetch_public_page(body["query"])
                page["artifact"] = manager.archive_web_page(page)
                report = page_slice(page, 0)
            else:
                report = await manager.video_worker.search(manager.runner_password(),
                    manager.creds.values["opencode_key"], validate_task_query(body["query"]),
                    search=manager.search_web, archive_page=manager.archive_web_page)
        except (ClientError, TimeoutError, KeyError, TypeError):
            raise RuntimeError("DSH 未返回可核实的网页资料") from None
        return web.json_response(report, headers={"Cache-Control": "no-store"})

    async def delegate_desktop(request):
        body = await request.clone(client_max_size=WORKER_BODY_LIMIT).json()
        if not isinstance(body, dict) or body.keys() - {"agent"} != {"instruction"}:
            raise ValueError("只接受专用 Windows 桌面任务说明")
        agent = validate_agent(body["agent"]) if "agent" in body else None
        pairing = manager.creds.values.get("desktop_token")
        if not pairing:
            raise ValueError("专用 Windows 桌面尚未配对")
        language = shared_language.prompt(manager.activity) if agent else ""
        report = await manager.video_worker.desktop(manager.runner_password(), manager.creds.values["opencode_key"],
                                                     pairing, body["instruction"], agent=agent, language=language)
        return web.json_response(report, headers={"Cache-Control": "no-store"})

    async def go_usage(_request):
        value = await manager.go_subscription_usage()
        return web.json_response(value, headers={"Cache-Control": "no-store"})

    async def sync_observatory(_app):
        async def loop():
            while True:
                try:
                    await manager.sync_room()
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    # The existing cache is marked stale by sync_room().
                    pass
                await asyncio.sleep(60)

        async def backup_loop():
            while True:
                if not manager.archive_lock.locked():
                    async with manager.archive_lock:
                        try:
                            if manager.room_snapshot is not None:
                                await asyncio.to_thread(manager.create_archive, "sync")
                            else:
                                await asyncio.to_thread(daily_backup, manager.astro)
                        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                            pass
                await asyncio.sleep(900)

        async def settings_loop():
            while True:
                if manager.settings_sync.base is not None:
                    await manager.settings_sync.check()
                await asyncio.sleep(60)

        tasks = []
        if manager.room_snapshot is not None:
            tasks.append(asyncio.create_task(loop()))
        if manager.astro.is_dir():
            tasks.append(asyncio.create_task(backup_loop()))
        if manager.settings_sync is not None:
            tasks.append(asyncio.create_task(settings_loop()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if manager.settings_sync is not None:
                await manager.settings_sync.peer.close()

    app.cleanup_ctx.append(sync_observatory)

    async def home(_request):
        page = PAGE.read_text(encoding="utf-8")
        if manager.dev.parent == DESKTOP_HOME:
            page = page.replace('id="recovery" hidden', 'id="recovery"').replace(
                'id="recovery-hint" class="muted hint" hidden', 'id="recovery-hint" class="muted hint"')
            page = page.replace('id="sync-backup" hidden', 'id="sync-backup"')
            page = page.replace('id="export-thinkbook" hidden', 'id="export-thinkbook"')
        if _request.path == "/":
            page = workspace_web.page(page, "台式机本机" if manager.dev.parent == DESKTOP_HOME else "ThinkBook 正式机")
        return web.Response(text=page, content_type="text/html",
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"})

    async def icon(_request):
        return web.FileResponse(ICON)

    async def meeting_page(_request):
        target = "台式机本机" if manager.dev.parent == DESKTOP_HOME else "ThinkBook 正式机"
        return meeting_web.page((59934, 59935), target)

    async def owner_work_page(_request):
        target = "台式机本机" if manager.dev.parent == DESKTOP_HOME else "ThinkBook 正式机"
        return owner_work.page((59934, 59935), target)

    async def owner_chat_page(_request):
        target = "台式机本机" if manager.dev.parent == DESKTOP_HOME else "ThinkBook 正式机"
        return owner_work.page((59934, 59935), target, chat=True)

    async def owner_work_api(request):
        if manager.room_snapshot is not None:
            raise ValueError("请从 ThinkBook 控制台打开主人工作台")
        return await owner_work.handle(request, manager.owner_jobs, manager.owner_work, manager.owner_job_enabled)

    async def owner_chat_api(request):
        bridge = read_settings(manager.bridge_settings)
        scope = bridge.get("owner_scope")
        if manager.room_snapshot is not None or not scope or not manager.ledger.is_file():
            raise ValueError("请从已配置主人身份的 ThinkBook 控制台打开伙伴直聊")

        def access(body=None):
            persona_mode.read_owner_binding(manager.astro, scope)
            ready = all(bridge.get(key) for key in ("commission_cookie", "commission_workspace", "commission_go_file_token"))
            return owner_chat.access(manager.activity, scope, query=request.query, body=body, ready=ready)

        if request.method == "POST":
            body = await request.clone(client_max_size=16 * 1024).json()
            if not isinstance(body, dict):
                raise ValueError("需要主人直聊请求")
            async with manager.lock:
                data = await asyncio.to_thread(access, body)
        else:
            data = await asyncio.to_thread(access)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def status(_request):
        return web.json_response({"services": manager.statuses(), "official_key": bool(manager.creds.values.get("upstream_key")),
                                  "runner_password": bool(manager.runner_password())})

    async def resources(_request):
        snapshot = await asyncio.to_thread(host_resources.snapshot, manager.dev.parent)
        return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

    async def role_card(request):
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        if not scope or not manager.ledger.is_file() or manager.room_snapshot is not None:
            raise ValueError("请在 ThinkBook 控制台或 ThinkBook 远程控制中管理角色卡")
        action = request.query.get("action")
        ident = request.query.get("id", "")
        def download(body, filename, content_type, charset=None):
            return web.Response(body=body, content_type=content_type, charset=charset,
                                headers={"Content-Disposition": f'attachment; filename="{filename}"'})

        if request.method == "GET" and action not in {"download", "template", "history", "copy-status"}:
            raise ValueError("请选择角色卡下载")
        if action == "template":
            return download("# 角色卡\n\n写下角色是谁、人格、说话方式等。\n".encode("utf-8"),
                            "fishhome-role-template.md", "text/markdown", "utf-8")
        if request.method == "POST" and action not in {"preview", "create", "update-background", "preview-copy", "copy"}:
            raise ValueError("请选择预览、新建或更新背景")
        archive = None
        card = None
        if request.method == "POST":
            copy_upload = action in {"preview-copy", "copy"}
            maximum = dsh_history_archive.MAX_ARCHIVE if copy_upload else dsh_cards.MAX_PACKET
            content = await request.clone(client_max_size=maximum + 1).read()
            if copy_upload:
                archive = await asyncio.to_thread(dsh_history_archive.read_archive, content)
                card, identity, history, memories = archive
                if action == "preview-copy":
                    return web.json_response({"name": card["name"], "records": len(history),
                                              "memories": len(memories), "original_id": identity["id"]})
            else:
                card = dsh_cards.read_card_upload(content, request.query.get("filename", ""),
                                                  request.query.get("name", ""))
                if action == "preview":
                    return web.json_response({"name": card["name"], "files": list(card["background"]),
                                              "plain": bool(card.get("plain"))})
        store = ActivityStore(manager.activity, existing=True)
        with closing(store.db):
            roster = DshRoster(store, scope)
            if action == "download":
                profile = roster.get(ident)["profiles"][-1]
                ext = Path(dsh_cards.load_card(store, ident)["persona_file"]).suffix
                return download(profile["persona"].encode("utf-8"), f"fishhome-role-{ident}{ext}",
                                "text/plain" if ext == ".txt" else "text/markdown", "utf-8")
            if action == "history":
                body = dsh_history_archive.snapshot(store, scope, ident)
                return download(body, f"fishhome-history-{ident}.zip", "application/zip")
            if action == "copy-status":
                roster.get(ident)
                return web.json_response(dsh_history_archive.import_status(store, scope, ident))
            if action == "copy":
                return web.json_response(dsh_history_archive.copy_from_archive(
                    store, scope, archive, request.query.get("name", "")))
            if action == "update-background" and card.get("plain"):
                row = roster.update(ident, persona=card["persona"])
                return web.json_response({"name": row["name"], "id": row["id"], "files": []})
            with store.transaction():
                if action == "create":
                    row = roster.create(card["name"], card["persona"])
                else:
                    record = roster.get(ident)
                    row = roster.public(record)
                    if record["name"] != card["name"] or record["profiles"][-1]["persona"] != card["persona"]:
                        raise ValueError("角色卡的名字及人格与所选小弟不同")
                dsh_cards.import_card_files(store, scope, row["id"], card["name"], card)
        return web.json_response({"name": row["name"], "id": row["id"], "files": list(card["background"])})

    async def team_library(request):
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        if not scope or not manager.ledger.is_file() or manager.room_snapshot is not None:
            raise ValueError("请从 ThinkBook 控制台管理角色和多人故事")
        store = ActivityStore(manager.activity, existing=True)
        with closing(store.db):
            if request.method == "GET":
                ident = request.query.get("id")
                kind = request.query.get("document")
                if kind == "impressions" and ident in {None, dsh_cards.FISH_PARTICIPANT}:
                    text = dsh_cards.load_fish_impressions(store)
                    return web.json_response({"status": "empty"} if text is None else
                                             {"status": "available", "text": text, "filename": "大肥鱼-他人印象.txt"})
                if kind in {"story", "impressions"}:
                    agent = DshRoster(store, scope).get(ident)
                    filenames = dsh_cards.STORY_FILES if kind == "story" else dsh_cards.IMPRESSION_FILES
                    for filename, text in dsh_cards.load_card(store, ident)["background"].items():
                        if filename in filenames:
                            return web.json_response({"status": "available", "text": text,
                                                      "filename": agent["name"] + "-" + filename})
                    return web.json_response({"status": "empty"})
                return web.json_response(dsh_cards.get_story(store, scope, ident) if ident
                                         else {"stories": dsh_cards.stories(store, scope)})
            body = await request.clone(client_max_size=256 * 1024).json()
            if not isinstance(body, dict):
                raise ValueError("无效的角色资料请求")
            action = body.get("action")
            ident = body.get("id", "")
            status = body.get("status")
            if action == "save-fish-impressions" or (action == "save-impressions" and ident == dsh_cards.FISH_PARTICIPANT):
                filename = body.get("filename")
                if not isinstance(filename, str) or Path(filename).suffix.lower() not in {".md", ".txt"}:
                    raise ValueError("请选择 UTF-8 的 .md 或 .txt 文本")
                dsh_cards.save_fish_impressions(store, body.get("text"))
                return web.json_response({"status": "saved"})
            if action in {"save-personal-story", "save-impressions"}:
                kind = "story" if action == "save-personal-story" else "impressions"
                with store.transaction():
                    profile = DshRoster(store, scope).get(ident)["profiles"][-1]
                    dsh_cards.save_personal_text(store, ident, profile, kind, body.get("filename"), body.get("text"))
                return web.json_response({"status": "saved"})
            if action == "role-status" and status in {"active", "archived"}:
                return web.json_response(DshRoster(store, scope).update(ident, status=status))
            if action == "story-status" and ident and status in {"active", "archived"}:
                with store.transaction():
                    return web.json_response(dsh_cards.set_story(store, scope, ident=ident, status=status))
            if action == "save-story":
                with store.transaction():
                    row = dsh_cards.set_story(store, scope, ident=ident, filename=body.get("filename"),
                                              text=body.get("text"), participants=body.get("participants"))
                return web.json_response(row)
            raise ValueError("请选择保存、归档或恢复")

    async def switch_persona(request):
        if manager.room_snapshot is not None:
            return web.json_response({"mode": "remote"})
        view = persona_mode.status(manager.astro)
        if request.method == "GET":
            return web.json_response(view)
        body = await request.json()
        if (not isinstance(body, dict) or body.keys() - {"mode", "archive_existing"}
                or body.get("mode") not in {"formal", "test"}
                or type(body.get("archive_existing", False)) is not bool):
            raise ValueError("请选择正式或测试人格")
        mode = body["mode"]
        async with manager.lock:
            view = persona_mode.status(manager.astro)
            if view["mode"] == "unavailable":
                raise ValueError("当前人格没有可切换的同版本配对")
            if view["mode"] == mode:
                return web.json_response(view)
            persona_mode.require_idle(manager.astro, manager.state)
            scope = read_settings(manager.bridge_settings).get("owner_scope")
            if not scope:
                raise ValueError("没有配置主人私聊")
            persona_mode.read_owner_binding(manager.astro, scope)
            room_running = any(item["id"] == "room" and item["state"] == "running"
                               for item in manager.statuses())
            await manager.stop("astrbot")
            try:
                command = [str(manager.python), "-X", "utf8", "-B", "-m", "deploy.desktop_manager.persona_mode",
                           str(manager.astro), str(manager.state), mode, scope]
                if body.get("archive_existing") and mode == "formal":
                    command.append("--archive-existing")
                child = await asyncio.create_subprocess_exec(*command, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                output, _ = await child.communicate()
                if child.returncode:
                    lines = output.decode("utf-8", errors="replace").splitlines()
                    detail = lines[-1].removeprefix("ValueError: ") if lines and lines[-1].startswith("ValueError: ") else "人格切换失败，请查看控制台日志。"
                    print("persona switch failed with exit code", child.returncode, flush=True)
                    raise ValueError(detail)
            finally:
                await manager.start("astrbot")
            if room_running:
                await manager.stop("room")
                await manager.start("room")
        return web.json_response({**persona_mode.status(manager.astro), "message": "已切换人格，正在恢复 QQ 连接。"})

    async def switch_provider(request):
        if manager.room_snapshot is not None:
            return web.json_response({"mode": "remote", "routes": []})
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        if request.method == "GET":
            return web.json_response(provider_mode.status(manager.astro, scope))
        body = await request.json()
        if not isinstance(body, dict) or body.keys() != {"mode"} or body["mode"] not in ("go", "deepseek"):
            raise ValueError("请选择 OpenCode Go 或官方 DeepSeek")
        mode = body["mode"]
        async with manager.lock:
            if mode == "go" and not manager.creds.values.get("opencode_key"):
                raise ValueError("请先在控制台保存 OpenCode Go Key")
            if provider_mode.status(manager.astro, scope)["mode"] == mode:
                return web.json_response(provider_mode.status(manager.astro, scope))
            provider_mode.resolve(manager.astro, scope, mode)
            persona_mode.require_idle(manager.astro, manager.state, operation="切换模型")
            was_running = any(item["id"] == "astrbot" and item["state"] == "running"
                              for item in manager.statuses())
            if was_running:
                await manager.stop("astrbot")
            try:
                child = await asyncio.create_subprocess_exec(
                    str(manager.python), "-X", "utf8", "-B", "-m", "deploy.desktop_manager.provider_mode",
                    str(manager.astro), scope, mode, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
                output, _ = await child.communicate()
                if child.returncode:
                    print("provider switch failed with exit code", child.returncode, flush=True)
                    detail = output.decode("utf-8", errors="replace").splitlines()
                    if detail and detail[-1].startswith("ValueError: "):
                        raise ValueError(detail[-1].removeprefix("ValueError: "))
                    raise ValueError("模型切换失败，已恢复原路线。")
            finally:
                if was_running:
                    await manager.start("astrbot")
        return web.json_response({**provider_mode.status(manager.astro, scope), "message": "模型已切换。"})

    async def language_settings(request):
        if request.method == "GET":
            return web.json_response(shared_language.settings(manager.activity))
        body = await request.clone(client_max_size=320 * 1024).json()
        if (not isinstance(body, dict) or set(body) not in ({"enabled"}, {"enabled", "filename", "text"})
                or type(body["enabled"]) is not bool):
            raise ValueError("请提供共同语言启用状态或新的文本文件")
        async with manager.lock:
            config = {**shared_language.settings(manager.activity), **body}
            if body == {"enabled": False} and not config["filename"]:
                return web.json_response(config)
            shared_language.validate(config["filename"], config["text"])
            manager.activity.mkdir(parents=True, exist_ok=True)
            write_json(manager.activity / shared_language.FILE, config)
        return web.json_response(config)

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
                if item == "desktop" and mode == "start" and not manager.options.get("desktop_auto_enabled"):
                    continue
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
            if name in {"upstream_key", "opencode_key"} and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{12,250}", value):
                manager.creds.values[name] = value
                manager.creds.save()
                if name == "opencode_key":
                    manager.sync_video_worker()
                    message = "已加密保存 OpenCode Key。重启人民币计费和 AstrBot 后使用新 Key，模型选择不变。"
                else:
                    message = "已加密保存 DeepSeek Key，请重启计费服务以启用。"
            elif name in {"runner_password", "compute_password", "desktop_password"} and isinstance(value, str) and 0 < len(value) <= 256:
                account = {"compute_password": compute.ACCOUNT, "desktop_password": "FishHomeDesktop"}.get(name, RUNNER)
                with impersonate(account, value):
                    pass
                manager.creds.values[name] = value
                manager.creds.save()
                if name == "runner_password":
                    manager.sync_video_worker()
                message = "已验证并加密保存账户密码。"
            elif name == "desktop_auto_enabled" and type(value) is bool:
                if value and not manager.creds.values.get("desktop_password"):
                    raise ValueError("请先保存 FishHomeDesktop 标准账户密码")
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                message = "已保存 Windows 桌面随全部服务启动的设置。"
            elif name == "dsh_go_files_enabled" and type(value) is bool:
                if value and not manager.creds.values.get("opencode_key"):
                    raise ValueError("请先保存 OpenCode Go Key")
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                manager.sync_video_worker()
                message = "已保存。先重启计费服务，再重启两路文件 DSH。"
            elif name == "dsh_asr_free_only_confirmed" and type(value) is bool:
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                message = "已保存百炼免费额度用完即停的确认状态。"
            elif name == "desktop_token" and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
                manager.creds.values[name] = value
                manager.creds.save()
                manager.sync_video_worker()
                message = "已加密保存专用桌面配对凭据。"
            elif name == "opencode_server_password" and isinstance(value, str) and 1 <= len(value) <= 250:
                manager.creds.values[name] = value
                manager.creds.save()
                message = "已加密保存 OpenCode 本机额度查询凭据。"
            elif name == "opencode_server_port" and type(value) is int and 1 <= value <= 65535:
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                message = "已保存 OpenCode 本机服务端口。"
            elif name in WORKER_SWITCHES.values() and type(value) is bool:
                if name == "dsh_desktop_enabled" and value and not manager.creds.values.get("desktop_token"):
                    raise ValueError("请先保存专用 Windows 桌面配对凭据")
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                available = bool(manager.sync_video_worker()) and value
                label = {"dsh_video_enabled": "视频代看", "dsh_web_search_enabled": "网页搜索",
                         "dsh_desktop_enabled": "Windows 桌面"}[name]
                message = f"已启用 DSH {label}。" if available else f"DSH {label}当前不可用。"
            elif name == "compute_enabled" and type(value) is bool:
                if value and not manager.creds.values.get("compute_password"):
                    raise ValueError("请先保存 FishHomeCompute 的密码")
                manager.options[name] = value
                write_json(manager.options_path, manager.options)
                if not value:
                    manager.update_bridge(life_compute_enabled=False, commission_compute_enabled=False)
                message = "已保存。重启两路 DSH 使配置生效。"
            else:
                message = manager.set_config(name, value)
        return web.json_response({"message": message})

    async def configuration(_request):
        return web.json_response(manager.configuration(), headers={"Cache-Control": "no-store"})

    async def media_models(request):
        headers = {"Cache-Control": "no-store"}
        face_path = manager.astro / model_settings.FACE_PATH

        def snapshot():
            return model_settings.status(read_settings(manager.astrbot_settings), read_settings(face_path),
                                         manager.creds.values, manager.options.get("dsh_asr_free_only_confirmed") is True)

        if request.method == "GET":
            return web.json_response(snapshot(), headers=headers)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("需要模型设置对象")
        action = body.pop("action", None)
        if action == "catalog" and not body:
            models = await model_settings.go_models(manager.creds.values.get("opencode_key"))
            return web.json_response({"models": models}, headers=headers)
        if action == "probe" and body.keys() == {"provider"} and body["provider"] in (
                "tts", "asr", "dsh_asr", "deepseek", "go"):
            message = await model_settings.probe(manager, body["provider"], read_settings(manager.astrbot_settings),
                                                 read_settings(face_path))
            return web.json_response({"message": message}, headers=headers)
        if action == "save-go" and body.keys() == {"model"}:
            async with manager.lock:
                cfg = read_settings(manager.astrbot_settings)
                provider = next((p for p in cfg.get("provider", []) if p.get("id") == provider_mode.GO_PROVIDER), None)
                if not provider or not provider.get("enable"):
                    raise ValueError("请先在 AstrBot 启用现有 OpenCode Go 提供商")
                if body["model"] != provider.get("model"):
                    models = await model_settings.go_models(manager.creds.values.get("opencode_key"))
                    if body["model"] not in models:
                        raise ValueError("请选择 Go 模型目录中兼容的模型")
                    provider["model"] = body["model"]
                    write_json(manager.astrbot_settings, cfg)
            return web.json_response({**snapshot(), "message": "已保存。重启 AstrBot 后使用新模型。"}, headers=headers)
        if action != "save-speech":
            raise ValueError("未知的模型设置操作")
        async with manager.lock:
            cfg, face = read_settings(manager.astrbot_settings), read_settings(face_path)
            previous_face = face.copy()
            tts = model_settings.tts_config(cfg)
            previous_tts = tts.copy() if tts is not None else None
            model_settings.validate_speech(body, cfg, face)
            if previous_face != face and not (face_path.parent / "main.py").is_file():
                raise ValueError("当前主机未安装 QQ 听写插件")
            original = None
            if previous_tts != tts:
                original = manager.astrbot_settings.read_bytes()
                write_json(manager.astrbot_settings, cfg)
            try:
                if previous_face != face:
                    write_json(face_path, face)
            except OSError:
                if original is not None:
                    atomic_write(manager.astrbot_settings, original)
                raise
        return web.json_response({**snapshot(), "message": "已保存。QQ 语音在重启 AstrBot 后生效。"},
                                 headers=headers)

    async def dsh_team(_request):
        view = _request.query.get("view", "current")
        if view not in {"current", "test", "formal"}:
            raise ValueError("请选择正式或测试记录")
        current = configured_mode(manager.astrbot_settings)
        group = current if view == "current" else view
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        if not scope or not manager.ledger.is_file():
            return web.json_response({"agents": [], "fish_documents": None, "jobs": [], "meetings": [],
                                      "group": group, "current": current})
        roster_prefix = f"dsh-roster:v1:{scope}:"
        import_prefix = f"{COPY_MEMORY_PREFIX}{scope}:"
        job_prefix = f"commissioned-file:v1:{scope}:"
        def read():
            store = ActivityStore(manager.activity, existing=True, read_only=True)
            with closing(store.db) as db:
                def role_documents(ident):
                    try:
                        background = dsh_cards.load_card(store, ident)["background"]
                    except (OSError, ValueError):
                        return None
                    return {
                        kind: next((name for name in background if name in files), None)
                        for kind, files in (("story", dsh_cards.STORY_FILES), ("impressions", dsh_cards.IMPRESSION_FILES))
                    }

                try:
                    fish_documents = {"impressions": "大肥鱼-他人印象.txt" if
                                      dsh_cards.load_fish_impressions(store) is not None else None}
                except (OSError, ValueError):
                    fish_documents = None
                agents = db.execute(
                    "SELECT json_extract(value,'$.id'), json_extract(value,'$.name'), "
                    "json_extract(value,'$.status'), json_extract(value,'$.version'), json_extract(value,'$.persona_mode') "
                    "FROM settings WHERE substr(key,1,?)=? ORDER BY json_extract(value,'$.created')",
                    (len(roster_prefix), roster_prefix),
                )
                jobs = db.execute(
                    "SELECT json_extract(value,'$.id'), json_extract(value,'$.source'), "
                    "json_extract(value,'$.state'), json_extract(value,'$.delivery'), "
                    "COALESCE(json_extract(value,'$.job_kind'),'file'), "
                    "COALESCE(json_extract(value,'$.agent.name'),json_extract(value,'$.spec.agent.name')), "
                    "json_extract(value,'$.project') "
                    "FROM settings WHERE substr(key,1,?)=? " + GROUP_SQL
                    + "ORDER BY json_extract(value,'$.created') DESC LIMIT 20",
                    (len(job_prefix), job_prefix, group, group),
                )
                imports = {row[0][len(import_prefix):]: {"state": row[1], "copied": row[2], "total": row[3], "error": row[4]}
                           for row in db.execute(
                               "SELECT key, json_extract(value,'$.state'), json_extract(value,'$.next'), "
                               "json_extract(value,'$.total'), json_extract(value,'$.error') "
                               "FROM settings WHERE substr(key,1,?)=?", (len(import_prefix), import_prefix))}
                return {"group": group, "current": current, "fish_documents": fish_documents,
                        "agents": [{**dict(zip(("id", "name", "status", "version", "persona_mode"), row)),
                                    "documents": role_documents(row[0]),
                                    **({"copy_import": imports[row[0]]} if row[0] in imports else {})} for row in agents],
                        "jobs": [dict(zip(("id", "source", "state", "delivery", "job_kind", "agent", "project"), row))
                                 for row in jobs],
                        "meetings": meeting_list(db, scope, group)}

        try:
            return web.json_response(await asyncio.to_thread(read))
        except sqlite3.Error:
            return web.json_response({"error": "暂时无法读取 DSH 小队和任务"}, status=503)

    async def overview(_request):
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        group = configured_mode(manager.astrbot_settings)
        try:
            return web.json_response(await asyncio.to_thread(dashboard.read_activity, manager.ledger, scope, group),
                                     headers={"Cache-Control": "no-store"})
        except sqlite3.Error:
            return web.json_response({"error": "暂时无法读取活动记录"}, status=503)

    def meeting_list(db, scope, group):
        prefix = f"dsh-meeting:v1:{scope}:"
        rows = db.execute(
            "WITH meetings AS (SELECT json_extract(value,'$.id') AS id, "
            "json_extract(value,'$.topic') AS topic, json_extract(value,'$.status') AS status, "
            "json_extract(value,'$.round') AS round, json_extract(value,'$.rounds') AS rounds, "
            "json_extract(value,'$.style') AS style, "
            "max(coalesce(json_extract(value,'$.created'),0), coalesce(json_extract(value,'$.last_notice'),0), "
            "coalesce(json_extract(value,'$.transcript[#-1].at'),0)) AS observed, "
            "json_extract(value,'$.status') IN ('queued','running','go_wait','stopping','paused') AS active "
            "FROM settings WHERE substr(key,1,?)=? " + GROUP_SQL +
            "), ranked AS (SELECT *, row_number() OVER (PARTITION BY active ORDER BY observed DESC) AS rank "
            "FROM meetings) SELECT id, topic, status, round, rounds, style FROM ranked "
            "WHERE active OR rank <= 30 ORDER BY active DESC, observed DESC",
            (len(prefix), prefix, group, group))
        return [dict(zip(("id", "topic", "status", "round", "rounds", "style"), row)) for row in rows]

    async def dsh_meeting_live(request):
        current = configured_mode(manager.astrbot_settings)
        view = request.query.get("view", "current")
        if view not in {"current", "test", "formal"}:
            raise ValueError("请选择正式或测试记录")
        group = current if view == "current" else view
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        ident = request.query.get("id")
        writing = request.method == "POST"
        if ident is None and not writing:
            def read():
                if not scope or not manager.ledger.is_file():
                    return []
                with closing(sqlite3.connect(manager.ledger.as_uri() + "?mode=ro", uri=True, timeout=4)) as db:
                    return meeting_list(db, scope, group)

            response = web.json_response({"meetings": await asyncio.to_thread(read), "group": group,
                                          "current": current}, headers={"Cache-Control": "no-store"})
            if hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
                meeting_web.grant(response, token, PORT)
            return response
        if not scope or not manager.ledger.is_file():
            raise ValueError("找不到这场会议")
        after = int(request.query.get("after", 0))
        body = await request.json() if writing else None
        if writing and (not isinstance(body, dict) or set(body) != {"text", "post_id"}
                        or not isinstance(body["post_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", body["post_id"])):
            raise ValueError("请提供本次插话和编号")

        def access():
            if writing and group != configured_mode(manager.astrbot_settings):
                raise ValueError("请切换到这场会议所属人格后再插话")
            store = ActivityStore(manager.activity, existing=True, read_only=not writing)
            with closing(store.db):
                meeting = DshMeetings(store, scope, view=group)
                if writing:
                    meeting.post(ident, body["text"], "web:" + body["post_id"], owner_post=True)
                result = meeting.live(ident, after)
                result["can_post"] = result["can_post"] and group == current
                return result

        try:
            return web.json_response(await asyncio.to_thread(access), headers={"Cache-Control": "no-store"})
        except sqlite3.Error:
            return web.json_response({"error": "暂时无法打开组会，请稍后重试"}, status=503)

    async def cancel_public_team_job(request):
        body = await request.json()
        if (not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str)
                or not re.fullmatch(r"[0-9a-f]{12}", body["id"])):
            raise ValueError("请提供一条有效的公开资料任务 ID")
        scope = read_settings(manager.bridge_settings).get("owner_scope")
        if not scope or not manager.ledger.is_file():
            raise ValueError("找不到这项公开资料任务")
        key = f"commissioned-file:v1:{scope}:{body['id']}"

        def lookup():
            mode = configured_mode(manager.astrbot_settings)
            with closing(sqlite3.connect(manager.ledger.as_uri() + "?mode=ro", uri=True, timeout=4)) as db:
                return db.execute(
                    "SELECT json_extract(value,'$.task_id') FROM settings WHERE key=? "
                    + GROUP_SQL +
                    "AND json_extract(value,'$.job_kind') IN ('video','web') "
                    "AND json_extract(value,'$.state') IN ('prepared','dispatching','queued','running','cancelling')",
                    (key, mode, mode),
                ).fetchone()

        row = await asyncio.to_thread(lookup)
        if not row:
            raise ValueError("任务已结束或不在当前主人的队列中")
        result = manager.public_jobs.cancel(row[0])
        return web.json_response({"id": body["id"], "state": result["state"]})

    async def shared_settings(request):
        if request.method == "GET":
            values = await asyncio.to_thread(manager.shared_settings.snapshot)
        else:
            body = await request.json()
            if not isinstance(body, dict) or body.keys() != {"expected", "changes"}:
                raise ValueError("设置请求格式不正确")
            async with manager.lock:
                values = await asyncio.to_thread(manager.shared_settings.apply, body["expected"], body["changes"])
        return web.json_response(values, headers={"Cache-Control": "no-store"})

    async def settings_sync(request):
        sync = manager.settings_sync
        if sync is None:
            return web.json_response({"error": "请在台式机本机控制台管理同步"}, status=404)
        if request.method == "GET":
            result = sync.view
        else:
            body = await request.json()
            result = await sync.check(confirmation=body if body else None)
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    async def diagnostics(_request):
        return web.json_response({"checks": await manager.diagnostics()}, headers={"Cache-Control": "no-store"})

    async def dsh_ui(_request):
        service = manager.specs()["dsh"]
        if not any(manager.matching_pid(service, pid) for pid in manager.listeners().get(service.port, ())):
            raise ValueError("请先启动 DeepSeek Harness")
        ticket = manager.dsh_launch_token()
        if not ticket:
            raise ValueError("DSH 登录票据尚未就绪，请重试或重启 DSH")
        response = web.json_response({"url": "http://127.0.0.1:59934/?" + urlencode({"token": ticket})},
                                     headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        meeting_web.grant(response, token, PORT)
        owner_work.grant(response, token, PORT)
        return response

    async def archive_history(_request):
        pointer = manager.backups / "thinkbook-last-verified.json"
        return web.json_response({"backups": await asyncio.to_thread(manager.archive_history),
                                  "last_verified": read_settings(pointer)})

    async def archive(request):
        kind = request.match_info["kind"]
        if kind not in {"backup", "export", "recovery", "sync", "export-thinkbook"} or (
                kind in {"recovery", "sync", "export-thinkbook"} and manager.dev.parent != DESKTOP_HOME):
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
    app.router.add_get("/classic", home)
    app.router.add_get("/icon.png", icon)
    app.router.add_get("/api/ping", ping)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/host-resources", resources)
    app.router.add_get("/api/dashboard", overview)
    app.router.add_post("/api/services/{name}/{mode}", action)
    app.router.add_post("/api/config", configure)
    app.router.add_get("/api/persona-mode", switch_persona)
    app.router.add_post("/api/persona-mode", switch_persona)
    app.router.add_route("GET", "/api/fish-model-route", switch_provider)
    app.router.add_route("POST", "/api/fish-model-route", switch_provider)
    app.router.add_get("/api/shared-language", language_settings)
    app.router.add_post("/api/shared-language", language_settings)
    app.router.add_get("/api/meme-dictionary", meme_dictionary)
    app.router.add_post("/api/meme-dictionary", meme_dictionary)
    app.router.add_post(VIDEO_PATH, delegate_video)
    app.router.add_post(SEARCH_PATH, delegate_search)
    app.router.add_post(DESKTOP_PATH, delegate_desktop)
    for method in ("PUT", "GET", "DELETE"):
        app.router.add_route(method, PUBLIC_JOBS_PATH, public_job)
    app.router.add_get(GO_USAGE_PATH, go_usage)
    app.router.add_get("/api/config", configuration)
    app.router.add_get("/api/dsh-team", dsh_team)
    app.router.add_get(meeting_web.PAGE, meeting_page)
    app.router.add_get(owner_work.PAGE, owner_work_page)
    app.router.add_get(owner_work.CHAT_PAGE, owner_chat_page)
    app.router.add_get(owner_work.API, owner_work_api)
    app.router.add_put(owner_work.API, owner_work_api)
    app.router.add_delete(owner_work.API, owner_work_api)
    app.router.add_get(owner_work.CHAT_API, owner_chat_api)
    app.router.add_post(owner_work.CHAT_API, owner_chat_api)
    app.router.add_get(meeting_web.API, dsh_meeting_live)
    app.router.add_post(meeting_web.API, dsh_meeting_live)
    app.router.add_get("/api/dsh-team/role-card", role_card)
    app.router.add_post("/api/dsh-team/role-card", role_card)
    app.router.add_get("/api/dsh-team/library", team_library)
    app.router.add_post("/api/dsh-team/library", team_library)
    app.router.add_post("/api/dsh-team/cancel", cancel_public_team_job)
    app.router.add_get(PEER_PATH, shared_settings)
    app.router.add_post(PEER_PATH, shared_settings)
    app.router.add_get("/api/settings-sync", settings_sync)
    app.router.add_post("/api/settings-sync", settings_sync)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_post("/api/ui/dsh", dsh_ui)
    app.router.add_get("/api/archives", archive_history)
    app.router.add_post("/api/archives/{kind}", archive)
    app.router.add_post("/api/location", location)
    app.router.add_get("/api/budgets", budgets)
    app.router.add_post("/api/budgets", budgets)
    app.router.add_post("/api/exit", exit_console)
    app.router.add_get("/api/provider-keys", setup_info)
    app.router.add_get("/api/media-models", media_models)
    app.router.add_post("/api/media-models", media_models)
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
