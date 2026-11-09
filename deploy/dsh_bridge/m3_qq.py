"""AstrBot's owner-only connectivity command for the M3 DSH verification."""

import json
import os
from decimal import Decimal

import aiohttp

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .dsh_client import DshError
from .dsh_remote import RemoteDshClient


LIFE_BRIDGE = "fishhome_dsh_life_client"


@register("astrbot_plugin_dsh_bridge", "Fantasality / FishHome", "M3 DSH 连接验收", "1.0.0")
class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.settings = StarTools.get_data_dir("astrbot_plugin_dsh_bridge") / "m3.json"
        self._life_client = self.life_client

    async def initialize(self):
        setattr(self.context, LIFE_BRIDGE, self._life_client)

    async def terminate(self):
        if getattr(self.context, LIFE_BRIDGE, None) is self._life_client:
            delattr(self.context, LIFE_BRIDGE)

    def life_client(self, scope):
        cfg = json.loads(self.settings.read_text(encoding="utf-8"))
        if not scope or scope != cfg["owner_scope"]:
            raise PermissionError("DSH only serves the configured owner scope")
        return RemoteDshClient(session_cookie=cfg["session_cookie"])

    def owner_event(self, event):
        try:
            cfg = json.loads(self.settings.read_text(encoding="utf-8"))
            return (event.unified_msg_origin == cfg["owner_scope"]
                    and str(event.get_sender_id()) == cfg["owner_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    async def cost_balance(self):
        token = os.environ.get("FISH_COST_INTERACTION_TOKEN") or json.loads(
            self.settings.read_text(encoding="utf-8")).get("budget_token")
        if not token:
            return None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get("http://127.0.0.1:6197/balance",
                                   headers={"Authorization": f"Bearer {token}"},
                                   allow_redirects=False) as response:
                response.raise_for_status()
                return await response.json()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("额度")
    async def balance(self, event: AstrMessageEvent):
        if not self.owner_event(event):
            return
        event.stop_event()
        try:
            info = await self.cost_balance()
            reply = "人民币计费入口尚未配置。"
            if info:
                reply = f"预算日 {info['day']}（北京时间 04:00 切换）"
                for key, label in (("interaction", "交互"), ("autonomous", "自主")):
                    entry = info["ledgers"][key]
                    reply += f"\n{label}剩 ¥{entry['remaining']} / ¥{entry['limit']}，预留 ¥{entry['reserved']}"
        except (aiohttp.ClientError, TimeoutError, OSError, KeyError, ValueError):
            reply = "暂时无法查询人民币账本。"
        await event.send(event.plain_result(reply))

    @filter.on_llm_request(priority=100)
    async def warn_on_cost_limit(self, event: AstrMessageEvent, _request):
        if not self.owner_event(event) or event.get_extra("df_run_id"):
            return
        try:
            info = await self.cost_balance()
            if info and Decimal(info["ledgers"]["interaction"]["remaining"]) < Decimal(info["flash_request_reserve"]):
                event.stop_event()
                await event.send(event.plain_result("交互额度不足以预留下一次 Flash 请求。发送 /额度 查看余额。"))
        except (aiohttp.ClientError, TimeoutError, OSError, KeyError, ValueError, ArithmeticError):
            return

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("dsh")
    async def dsh(self, event: AstrMessageEvent):
        """Use /dsh ping to check the authenticated local DSH connection."""
        try:
            cfg = json.loads(self.settings.read_text(encoding="utf-8"))
            scope = cfg["owner_scope"]
            owner = cfg["owner_id"]
            cookie = cfg["session_cookie"]
        except (OSError, ValueError, KeyError, TypeError):
            return

        if event.unified_msg_origin != scope or str(event.get_sender_id()) != owner:
            return
        event.stop_event()
        if event.get_message_str().strip().lstrip("/").split() != ["dsh", "ping"]:
            await event.send(event.plain_result("M3 验收命令为 /dsh ping。"))
            return

        client = RemoteDshClient(session_cookie=cookie)
        try:
            info = await client.ping()
            reply = f"DSH 本机连接正常。会话 {info['sessions']} 个，运行中 {info['running']} 个。"
        except DshError as exc:
            reply = f"DSH 连接检查失败。{exc}"
        finally:
            await client.close()
        await event.send(event.plain_result(reply))
