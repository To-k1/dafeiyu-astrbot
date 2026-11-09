"""AstrBot's owner-only connectivity command for the M3 DSH verification."""

import json

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
