"""Connect FishHome to the installed Affective Core without owning mood state."""

from collections.abc import Mapping
import re
import time

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star, register


SERVICE = "dafeiyu_affect_integration_v1"
CORE_SERVICE = "_dafeiyu_affective_bridge"
CORE_VERSION = "affective_bridge_v1"
BACKGROUND = "【FishHome 心境背景】"


@register("astrbot_plugin_dafeiyu_affect_bridge", "FishHome", "FishHome affective integration", "0.1.0")
class FishAffectBridge(Star):
    version = 1

    def __init__(self, context, config=None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

    async def initialize(self):
        setattr(self.context, SERVICE, self)

    async def terminate(self):
        if getattr(self.context, SERVICE, None) is self:
            delattr(self.context, SERVICE)

    def owner_scope(self, scope):
        owner = str(self.config.get("owner_id") or "").strip()
        return (isinstance(scope, str) and scope == self.config.get("owner_scope") and
                re.fullmatch(r"[^:]+:FriendMessage:[0-9]+", scope) is not None and
                scope.rsplit(":", 1)[-1] == owner)

    def core(self):
        bridge = getattr(self.context, CORE_SERVICE, None)
        return bridge if getattr(bridge, "version", None) == CORE_VERSION else None

    def record_interest(self, scope, *, source_id, topic, polarity):
        if (self.config.get("life_events_enabled") is not True or not self.owner_scope(scope)
                or not source_id or type(polarity) is not int or polarity not in {-1, 1}):
            return None
        bridge = self.core()
        if bridge is None:
            return None
        kind = "interest_positive" if polarity == 1 else "interest_negative"
        return bridge.record_life_event(scope, kind, {"topic": topic, "source_id": source_id})

    @filter.on_llm_request(priority=-50)
    async def add_background(self, event, request):
        if self.config.get("chat_background_enabled") is not True:
            return
        scope = event.unified_msg_origin
        sender = event.get_sender_id()
        raw = getattr(event.message_obj, "raw_message", None)
        if (not self.owner_scope(scope) or not sender or str(sender) != scope.rsplit(":", 1)[-1]
                or event.get_platform_name() != "aiocqhttp"
                or not isinstance(raw, Mapping) or raw.get("post_type") != "message"
                or event.get_extra("df_run_id")):
            return
        prompt = request.system_prompt or ""
        if BACKGROUND in prompt:
            return
        bridge = self.core()
        if bridge is None:
            return
        try:
            snapshot = bridge.get_snapshot(scope=scope)
        except Exception as exc:
            logger.warning("FishAffectBridge snapshot unavailable: %s", type(exc).__name__)
            return
        if not snapshot or snapshot.get("scope") != scope:
            return
        state = snapshot["state"]
        mode = snapshot["expression"]["mode"]
        parts = []
        if mode not in {"neutral", "calm"}:
            parts.append(f"心境摘要为 {mode}。")
        stimulus = state.get("extra", {}).get("last_life_stimulus") or {}
        if (stimulus.get("kind") in {"interest_positive", "interest_negative"}
                and stimulus.get("topic") and 0 <= time.time() - stimulus.get("at", 0) <= 10800):
            parts.append("近期对一项已记录的活动表达过兴趣。" if stimulus["kind"] == "interest_positive"
                         else "近期对一项已记录的活动表达过不感兴趣。")
        if parts:
            request.system_prompt = (prompt + "\n" + BACKGROUND + "".join(parts)
                                     + "这些是背景资料。是否提起、怎样表达，由当前交流决定。")
