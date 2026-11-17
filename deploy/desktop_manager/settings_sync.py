"""Synchronize selected host settings without copying runtime data."""

import asyncio
import json
import socket
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone

from aiohttp import ClientError, ClientSession, ClientTimeout
import psutil

from deploy.bili_activity.ops import DEFAULTS, NUMERIC_LIMITS, exclusive, save_control, validate_dnd
from deploy.fish_cost_ledger import BUCKETS, DEFAULT_LIMIT, Ledger, amount, yuan
from .thinkbook import THINKBOOK_PORT, remote_token, ssh_args
from .windows import write_json


ACTIVITY_FIELDS = (
    "contact_daily_limit", "contact_unanswered_limit", "contact_cooldown_minutes", "contact_owner_quiet_minutes",
    "dnd_ranges", "daily_minutes", "daily_model_calls", "max_model_calls_per_run", "daily_shares",
    "project_active_limit", "project_max_stages", "project_review_delay_hours",
    "workshop_revisions_per_run", "workshop_max_characters",
)
BUDGET_FIELDS = {f"default_{bucket}_budget": bucket for bucket in BUCKETS}
FIELDS = (*ACTIVITY_FIELDS, *BUDGET_FIELDS)
PEER_PATH = "/api/shared-settings"


def normalized(values):
    if not isinstance(values, dict) or values.keys() - set(FIELDS):
        raise ValueError("发现不在同步范围内的设置")
    result = {}
    for key, value in values.items():
        if key in BUDGET_FIELDS:
            if not isinstance(value, str):
                raise ValueError("默认人民币额度应为金额字符串")
            result[key] = yuan(amount(value))
        elif key == "dnd_ranges":
            if value is not None:
                validate_dnd(value)
            result[key] = value
        else:
            low, high = NUMERIC_LIMITS[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 须为 {low}–{high} 的整数")
            result[key] = value
    return result


class SharedSettings:
    def __init__(self, control, ledger):
        self.control, self.ledger = control, ledger

    def activity(self):
        value = json.loads(self.control.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("活动配置不是 JSON 对象")
        return value

    @staticmethod
    def budget_defaults(db):
        rows = dict(db.execute("SELECT bucket, amount FROM fish_cost_limits WHERE day=''"))
        return {name: yuan(rows.get(bucket, DEFAULT_LIMIT)) for name, bucket in BUDGET_FIELDS.items()}

    def snapshot(self):
        if not self.control.is_file() or not self.ledger.is_file():
            raise ValueError("请先配置活动插件和人民币账本")
        control = DEFAULTS | self.activity()
        with closing(sqlite3.connect(self.ledger.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            budgets = self.budget_defaults(db)
        return normalized({key: control[key] for key in ACTIVITY_FIELDS} | budgets)

    def apply(self, expected, changes):
        expected, changes = normalized(expected), normalized(changes)
        if expected.keys() != changes.keys():
            raise ValueError("缺少修改前的设置值")
        if not self.control.is_file() or not self.ledger.is_file():
            raise ValueError("请先配置活动插件和人民币账本")
        activity_keys = changes.keys() - BUDGET_FIELDS.keys()
        if activity_keys:
            with exclusive(self.control.parent):
                control = self.activity()
                current = DEFAULTS | control
                update = {key: changes[key] for key in activity_keys
                          if current[key] == expected[key] and changes[key] != expected[key]}
                if update:
                    save_control(self.control.parent, control | update)
        if changes.keys() & BUDGET_FIELDS.keys():
            with Ledger(self.ledger).transaction() as db:
                current = self.budget_defaults(db)
                for key in changes.keys() & BUDGET_FIELDS.keys():
                    if current[key] == expected[key] and changes[key] != expected[key]:
                        db.execute("INSERT OR REPLACE INTO fish_cost_limits VALUES (?, '', ?)",
                                   (BUDGET_FIELDS[key], amount(changes[key])))
        return self.snapshot()


def reconcile(base, desktop, thinkbook):
    targets, conflicts = {}, []
    for key in FIELDS:
        if desktop[key] == thinkbook[key]:
            targets[key] = desktop[key]
        elif desktop[key] == base[key]:
            targets[key] = thinkbook[key]
        elif thinkbook[key] == base[key]:
            targets[key] = desktop[key]
        else:
            conflicts.append(key)
    return targets, conflicts


class ThinkBookPeer:
    def __init__(self):
        self.tunnel = self.http = None

    async def close(self):
        if self.http is not None:
            await self.http.close()
            self.http = None
        if self.tunnel is not None and self.tunnel.returncode is None:
            try:
                self.tunnel.terminate()
            except ProcessLookupError:
                pass
            await self.tunnel.wait()
        self.tunnel = None

    def is_listening(self):
        try:
            connections = psutil.Process(self.tunnel.pid).net_connections(kind="tcp")
        except psutil.NoSuchProcess:
            return False
        return any(row.status == psutil.CONN_LISTEN and row.laddr == ("127.0.0.1", self.port) for row in connections)

    async def request(self, body=None):
        try:
            if self.tunnel is None or self.tunnel.returncode is not None:
                await self.close()
                token = await asyncio.to_thread(remote_token)
                with socket.socket() as candidate:
                    candidate.bind(("127.0.0.1", 0))
                    self.port = candidate.getsockname()[1]
                self.tunnel = await asyncio.create_subprocess_exec(
                    *ssh_args(), "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                    "-L", f"127.0.0.1:{self.port}:127.0.0.1:{THINKBOOK_PORT}", "fishbook",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.http = ClientSession(timeout=ClientTimeout(total=8), headers={
                    "Host": f"127.0.0.1:{THINKBOOK_PORT}", "Authorization": f"Bearer {token}",
                })
                for _ in range(20):
                    if self.tunnel.returncode is not None:
                        raise OSError("ThinkBook 设置同步隧道无法启动")
                    if await asyncio.to_thread(self.is_listening):
                        break
                    await asyncio.sleep(.25)
                else:
                    raise OSError("ThinkBook 设置同步隧道未就绪")
            return await self.fetch(body)
        except (ClientError, OSError, RuntimeError, ValueError, psutil.Error, asyncio.TimeoutError):
            await self.close()
            raise

    async def fetch(self, body):
        async with self.http.request("GET" if body is None else "POST",
                                     f"http://127.0.0.1:{self.port}{PEER_PATH}", json=body) as response:
            if response.status == 404:
                raise ValueError("请更新 ThinkBook 控制台以启用设置同步")
            payload = await response.json()
            if response.status != 200:
                raise ValueError(payload.get("error", "ThinkBook 设置同步暂不可用"))
            values = normalized(payload)
            if values.keys() != set(FIELDS):
                raise ValueError("ThinkBook 同步字段与台式机不一致")
            return values


class SettingsSync:
    def __init__(self, local, path, peer):
        self.local, self.path, self.peer = local, path, peer
        self.lock = asyncio.Lock()
        self.base = None
        self.problem = None
        try:
            if path.is_file():
                candidate = normalized(json.loads(path.read_text(encoding="utf-8")))
                if candidate.keys() != set(FIELDS):
                    raise ValueError("共同设置记录字段不完整")
                self.base = candidate
        except (OSError, ValueError):
            self.problem = "之前的同步记录无法读取，设置同步已暂停。"
        self.view = {"initialized": self.base is not None, "rows": [], "last_checked": None, "error": self.problem}

    async def check(self, *, confirmation=None):
        async with self.lock:
            if self.problem:
                return self.view
            try:
                desktop = self.local.snapshot()
                thinkbook = await self.peer.request()
                if confirmation is not None:
                    return await self.confirm(desktop, thinkbook, confirmation)
                if self.base is not None:
                    targets, _ = reconcile(self.base, desktop, thinkbook)
                    desktop, thinkbook = await self.apply_targets(desktop, thinkbook, targets)
                    self.record_common(desktop, thinkbook)
                return self.report(desktop, thinkbook)
            except (ClientError, OSError, RuntimeError, ValueError, sqlite3.Error, psutil.Error, asyncio.TimeoutError, KeyError) as exc:
                self.view = {**self.view, "error": str(exc) if isinstance(exc, ValueError) else
                             "同步未完成，请检查两端控制台和 SSH。稍后会重试。"}
                return self.view

    async def confirm(self, desktop, thinkbook, confirmation):
        view = self.report(desktop, thinkbook)
        expected = {row["key"]: {"desktop": row["desktop"], "thinkbook": row["thinkbook"]} for row in view["rows"]}
        if (not isinstance(confirmation, dict) or confirmation.keys() != {"expected", "choices"}
                or confirmation["expected"] != expected):
            return {**view, "error": "设置已变化，请重新确认当前差异"}
        choices = confirmation["choices"]
        if not isinstance(choices, dict) or choices.keys() != expected.keys() or any(
            choice not in ("desktop", "thinkbook") for choice in choices.values()
        ):
            return {**view, "error": "请选择每项差异采用哪台机器的值"}
        targets = {key: desktop[key] if choice == "desktop" else thinkbook[key] for key, choice in choices.items()}
        desktop, thinkbook = await self.apply_targets(desktop, thinkbook, targets)
        if self.base is None and desktop != thinkbook:
            return {**self.report(desktop, thinkbook), "error": "值在确认期间发生变动，请重新预览"}
        self.record_common(desktop, thinkbook)
        return self.report(desktop, thinkbook)

    async def apply_targets(self, desktop, thinkbook, targets):
        local = {key: value for key, value in targets.items() if desktop[key] != value}
        remote = {key: value for key, value in targets.items() if thinkbook[key] != value}
        if local:
            desktop = self.local.apply({key: desktop[key] for key in local}, local)
        if remote:
            thinkbook = await self.peer.request({"expected": {key: thinkbook[key] for key in remote}, "changes": remote})
        return desktop, thinkbook

    def record_common(self, desktop, thinkbook):
        common = {key: desktop[key] for key in FIELDS if desktop[key] == thinkbook[key]}
        base = (self.base or {}) | common
        if base != self.base:
            write_json(self.path, base)
            self.base = base

    def report(self, desktop, thinkbook):
        keys = (reconcile(self.base, desktop, thinkbook)[1] if self.base is not None else
                [key for key in FIELDS if desktop[key] != thinkbook[key]])
        self.view = {"initialized": self.base is not None, "last_checked": datetime.now(timezone.utc).isoformat(),
                     "rows": [{"key": key, "desktop": desktop[key], "thinkbook": thinkbook[key]} for key in keys],
                     "error": None}
        return self.view
