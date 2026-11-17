"""Attach FishHome's outbox to the AstrBot OneBot adapter."""
import asyncio
from contextvars import ContextVar
from pathlib import Path
from time import monotonic

from aiocqhttp.exceptions import ActionFailed, ApiNotAvailable

from .qq_outbox import QQOutbox, QueuedDelivery


LIVE_GUARD = ContextVar("fishhome_qq_live_guard", default=None)


def install_outbox(bot, path: Path, owner_id):
    raw = bot.call_action
    healthy = {}

    def eligible(action, params):
        return (action in {"send_private_msg", "send_private_forward_msg"}
                or (action == "send_msg" and params.get("message_type") == "private"))

    def allowed(action, params):
        return eligible(action, params) and str(params.get("user_id", "")) == owner_id()

    async def ready(action, params):
        clients = bot._wsr_api_clients
        self_id = str(params.get("self_id") or "")
        if not self_id and len(clients) == 1:
            self_id = next(iter(clients))
        connection = clients.get(self_id)
        if connection is None:
            return False
        now = monotonic()
        if (cached := healthy.get(self_id)) and cached[0] is connection and now - cached[1] < 5:
            return cached[2]
        try:
            status = await asyncio.wait_for(raw("get_status", self_id=int(self_id)), 3)
            ok = isinstance(status, dict) and status.get("online") is True
        except Exception:
            ok = False
        healthy[self_id] = (connection, now, ok)
        return ok

    outbox = QQOutbox(path, send=raw, ready=ready, allowed=allowed,
                      unavailable_errors=(ApiNotAvailable,), rejected_errors=(ActionFailed,))

    async def submit(action, params, *, key=None, wait=0, live_guard=None, expires_in=None):
        explicit = params.get("self_id")
        clients = bot._wsr_api_clients
        if not explicit and len(clients) == 1:
            params = {**params, "self_id": int(next(iter(clients)))}
        try:
            return await outbox.submit(action, params, key=key, wait=wait, live_guard=live_guard,
                                       expires_in=expires_in)
        except QueuedDelivery as exc:
            if live_guard is not None:
                raise
            return {"fishhome_queue_id": exc.entry_id, "fishhome_queue_state": exc.state}

    async def call_action(action, **params):
        if not eligible(action, params):
            return await raw(action, **params)
        guard = LIVE_GUARD.get()
        return await submit(action, params, wait=15 if guard else 0, live_guard=guard)

    bot.call_action = call_action
    bot.fishhome_submit = submit
    bot.fishhome_outbox = outbox
    return outbox
