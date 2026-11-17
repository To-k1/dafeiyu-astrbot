"""Per-job, loopback model relay for a DSH worker with no OpenCode Key."""

import asyncio
import hmac
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from deploy.dsh_bridge.go_profile import BASE_URL, MODEL
from deploy.fish_cost import error, stream_usage


GO_SOURCES = {"go", "go-plus", "go-teams"}


@dataclass(frozen=True)
class GoReceipt:
    billing_source: str
    allowance: Decimal
    input_tokens: int
    output_tokens: int


def subscription_receipt(usage):
    billing = usage.get("opencode") if isinstance(usage, dict) else None
    if not isinstance(billing, dict) or billing.get("billing_source") not in GO_SOURCES:
        raise ValueError("缺少 OpenCode Go 订阅计费回执")
    try:
        cost = Decimal(str(usage["cost"]))
        allowance = Decimal(str(billing["allowance"]))
    except (KeyError, InvalidOperation):
        raise ValueError("OpenCode 未确认额外扣费和订阅额度") from None
    if cost != 0 or not allowance.is_finite() or allowance < 0:
        raise ValueError("OpenCode 回执未确认仅使用订阅额度")
    input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        raise ValueError("OpenCode 未确认 Token 用量")
    return GoReceipt(billing["billing_source"], allowance, input_tokens, output_tokens)


class GoRelay:
    def __init__(self, key, token, session_id, *, upstream=BASE_URL, on_receipt=None):
        if not key or len(token) < 24 or key == token or not session_id:
            raise ValueError("Go 工作进程需要独立的会话凭据")
        self.key = key
        self.authorization = f"Bearer {token}".encode()
        self.session_id = session_id
        self.upstream_url = upstream + "/chat/completions"
        self.receipts = []
        self.failure = None
        self.status = None
        self.on_receipt = on_receipt
        self.lock = asyncio.Lock()

    def create_app(self):
        app = web.Application(client_max_size=24 * 1024 * 1024)
        app.router.add_post("/v1/chat/completions", self.forward)
        app.cleanup_ctx.append(self.session_context)
        return app

    async def session_context(self, _app):
        async with ClientSession(timeout=ClientTimeout(total=120), trust_env=False) as client:
            self.client = client
            yield

    async def forward(self, request):
        if (not hmac.compare_digest(request.headers.get("Authorization", "").encode(), self.authorization)
                or request.headers.get("x-opencode-session") != self.session_id):
            return error(401, "unauthorized", "Go 工作会话未认证")
        try:
            body = await request.json()
        except ValueError:
            return error(400, "invalid_request", "Go 工作会话只接受 JSON 请求")
        if not isinstance(body, dict) or body.get("model") != MODEL or body.get("stream") is not True:
            return error(400, "invalid_request", "Go 工作会话只允许指定模型的流式请求")
        body["stream_options"] = {"include_usage": True}
        async with self.lock:
            if self.failure is not None:
                return error(409, "receipt_required", "上一请求的 Go 用量尚未核实")
            return await self.relay(request, body)

    async def relay(self, request, body):
        headers = {"Authorization": f"Bearer {self.key}", "x-opencode-session": self.session_id,
                   "User-Agent": "FishHome-DSH/0.2"}
        try:
            async with self.client.post(self.upstream_url, json=body, headers=headers, allow_redirects=False) as upstream:
                if upstream.status != 200:
                    self.failure = "OpenCode Go 拒绝请求"
                    self.status = upstream.status
                    return error(upstream.status if 400 <= upstream.status < 600 else 502, "go_error", self.failure)
                response, usage = await stream_usage(request, upstream, "openai")
        except (ClientError, TimeoutError):
            self.failure = "OpenCode Go 结果未确认"
            return error(502, "go_error", self.failure)
        try:
            receipt = subscription_receipt(usage)
            self.receipts.append(receipt)
            if self.on_receipt:
                self.on_receipt(receipt)
        except ValueError as exc:
            self.failure = str(exc)
        return response
