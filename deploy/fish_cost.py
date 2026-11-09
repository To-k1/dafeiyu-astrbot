"""Loopback gateway for metered DeepSeek Flash and explicit read-only tools."""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .fish_cost_ledger import BUCKETS, CostLimit, Ledger, yuan
from plugin.astrbot_plugin_bili_activity.cost_window import peak_end


OFFICIAL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
CONTEXT_LIMIT = 1_048_576
OUTPUT_LIMIT = 8192
REQUEST_TIMEOUT = 120
PRICE_FILE = Path(__file__).with_name("fish_cost_prices.json")
ENDPOINTS = {"openai": "/v1/chat/completions", "anthropic": "/anthropic/v1/messages",
             "search": "/v1/tools/search", "fetch": "/v1/tools/fetch"}


def load_prices(path):
    prices = json.loads(path.read_text(encoding="utf-8"))
    for band, keys in (("peak", {"miss", "hit", "output"}), ("off_peak", {"miss", "hit", "output"}),
                       ("tools", {"search", "fetch"})):
        rates = prices[band]
        if set(rates) != keys or any(type(v) is not int or not 0 < v <= 1_000_000_000 for v in rates.values()):
            raise ValueError(f"invalid tariff {band}")
    for band in ("peak", "off_peak"):
        if prices[band]["hit"] > prices[band]["miss"]:
            raise ValueError("cache hit tariff exceeds miss tariff")
    return prices


def normalize_model(body, kind):
    if body.get("model") != MODEL or body.get("n", 1) != 1 or "max_completion_tokens" in body:
        raise ValueError("仅允许单个 deepseek-flash 文本请求")
    if type(body.get("stream", False)) is not bool:
        raise ValueError("stream must be boolean")
    limit = body.get("max_tokens", OUTPUT_LIMIT)
    if type(limit) is not int or limit <= 0:
        raise ValueError("invalid output token limit")
    body["max_tokens"] = min(limit, OUTPUT_LIMIT)
    for tool in body.get("tools", []):
        local_tool = (tool.get("type") == "function" if kind == "openai" else
                      tool.get("type", "custom") == "custom" and "input_schema" in tool)
        if not local_tool:
            raise ValueError("服务器工具须经独立收费入口")
    for message in body["messages"]:
        blocks = message.get("content")
        if isinstance(blocks, list):
            for block in blocks:
                if block.get("type") not in {"text", "thinking", "redacted_thinking", "tool_use", "tool_result"}:
                    raise ValueError("受控入口仅接收文本和本地函数工具结果")
                if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                    if any(part.get("type") != "text" for part in block["content"]):
                        raise ValueError("受控入口仅接收文本工具结果")
    if kind == "openai" and body.get("stream"):
        body["stream_options"] = {"include_usage": True}
    return body["max_tokens"]


def tokens(kind, usage):
    if kind == "openai":
        total = usage["prompt_tokens"]
        hit = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        miss, out = total - hit, usage["completion_tokens"]
    else:
        hit = usage.get("cache_read_input_tokens", 0)
        miss, out = usage["input_tokens"] + usage.get("cache_creation_input_tokens", 0), usage["output_tokens"]
    counts = {"miss": miss, "hit": hit, "output": out}
    if any(type(n) is not int or n < 0 for n in counts.values()):
        raise ValueError("invalid upstream usage")
    return counts


async def stream_usage(request, upstream, kind):
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    usage, complete = {}, False
    try:
        async for line in upstream.content:
            if line.startswith(b"data:"):
                data = line[5:].strip()
                if data == b"[DONE]":
                    complete = kind == "openai"
                elif data:
                    try:
                        event = json.loads(data)
                        event_type = event.get("type")
                        if kind == "anthropic" and event_type == "message_start":
                            usage.update((event.get("message") or {}).get("usage") or {})
                        if kind == "anthropic" and event_type == "message_stop":
                            complete = True
                        if event.get("usage"):
                            usage.update(event["usage"])
                    except (ValueError, TypeError, AttributeError):
                        pass
            await response.write(line)
        await response.write_eof()
    except (ClientError, ConnectionError, TimeoutError, ValueError):
        return response, None
    return response, usage if complete else None


def error(status, code, message):
    return web.json_response({"type": "error", "error": {"type": code, "code": code, "message": message}},
                             status=status)


def create_app(ledger, prices, tokens_by_bucket, api_key, *, upstream_base=OFFICIAL, offline=False):
    """Bind credentials to ledgers, and reserve before making an upstream call."""
    if (set(tokens_by_bucket) != set(BUCKETS) or len(set(tokens_by_bucket.values())) != len(BUCKETS)
            or (not api_key and not offline)
            or any(len(value) < 24 or value == api_key for value in tokens_by_bucket.values())):
        raise ValueError("supply separate local credentials and a DeepSeek API key")

    session_key = web.AppKey("upstream", ClientSession)
    app = web.Application(client_max_size=8 * 1024 * 1024)

    async def session_context(application):
        async with ClientSession(timeout=ClientTimeout(total=REQUEST_TIMEOUT), trust_env=False) as session:
            application[session_key] = session
            yield

    def authorized(request, bucket):
        expected = tokens_by_bucket[bucket].encode()
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").encode()
        anthropic = request.headers.get("x-api-key", "").encode()
        return secrets.compare_digest(expected, bearer) or secrets.compare_digest(expected, anthropic)

    async def balance(request):
        if not authorized(request, "interaction"):
            return error(401, "authentication_error", "无权查询账本")
        return web.json_response({**ledger.snapshot(), "flash_request_reserve": yuan(reserve_model(OUTPUT_LIMIT))})

    def reserve_model(output):
        return max(r["miss"] * CONTEXT_LIMIT + r["output"] * output
                   for r in (prices["peak"], prices["off_peak"]))

    async def forward(request):
        bucket, kind = request.match_info["bucket"], request.match_info["kind"]
        if bucket not in BUCKETS or not authorized(request, bucket):
            return error(401, "authentication_error", "账本凭据无效")
        if offline:
            return error(503, "fish_cost_offline", "计费入口可查询余额，请先在本机配置 DeepSeek API Key。")
        tool = kind in prices["tools"]
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request must be a JSON object")
            if tool:
                maximum = prices["tools"][kind]
            else:
                output = normalize_model(body, kind)
                maximum = reserve_model(output)
        except (ValueError, KeyError, TypeError, AttributeError):
            return error(400, "invalid_request_error", "仅支持指定的 Flash 文本或独立搜索抓取请求")

        now = ledger.clock()
        try:
            ident = ledger.reserve(bucket, kind, maximum, now)
        except CostLimit as exc:
            return error(402, "fish_cost_limit", str(exc))

        headers = {"Authorization": f"Bearer {api_key}"}
        if kind == "anthropic":
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        session = request.app[session_key]
        try:
            async with session.post(upstream_base + ENDPOINTS[kind], json=body,
                                    headers=headers, allow_redirects=False) as upstream:
                if not 200 <= upstream.status < 300:
                    if tool and 400 <= upstream.status < 500:
                        ledger.settle(ident, 0)
                    return error(upstream.status if upstream.status >= 400 else 502,
                                 "upstream_error", "DeepSeek 未完成该请求")
                if not tool and body.get("stream"):
                    response, usage = await stream_usage(request, upstream, kind)
                else:
                    payload = await upstream.json()
                    response = web.json_response(payload)
                    usage = payload.get("usage") if not tool else None
                if tool:
                    field = "results" if kind == "search" else "content"
                    if field in payload:
                        ledger.settle(ident, maximum if payload[field] else 0)
                elif usage:
                    try:
                        counts = tokens(kind, usage)
                    except (KeyError, TypeError, ValueError):
                        return response  # Keep the reservation if the provider omitted usage.
                    # A request close to a transition can reach DeepSeek after it.
                    band = "peak" if peak_end(now) or peak_end(now + REQUEST_TIMEOUT) else "off_peak"
                    ledger.settle(ident, sum(prices[band][key] * value for key, value in counts.items()))
                return response
        except (ClientError, TimeoutError, ValueError):
            return error(502, "upstream_error", "DeepSeek 结果未确认，已保留预留额")

    app.cleanup_ctx.append(session_context)
    app.router.add_get("/balance", balance)
    app.router.add_post("/{kind:openai}/{bucket}/v1/chat/completions", forward)
    app.router.add_post("/{kind:anthropic}/{bucket}/v1/messages", forward)
    app.router.add_post("/tools/{bucket}/{kind:search|fetch}", forward)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=6197)
    serve.add_argument("--prices", type=Path, default=PRICE_FILE)
    serve.add_argument("--offline", action="store_true", help="Serve balances without forwarding paid requests")
    commands.add_parser("balance")
    adjust = commands.add_parser("adjust")
    adjust.add_argument("bucket", choices=BUCKETS)
    adjust.add_argument("amount")
    adjust.add_argument("--temporary", action="store_true")
    args = parser.parse_args()
    ledger = Ledger(args.db)
    if args.command == "serve":
        local_tokens = {bucket: os.environ["FISH_COST_" + bucket.upper() + "_TOKEN"] for bucket in BUCKETS}
        key = None if args.offline else os.environ["FISH_COST_UPSTREAM_KEY"]
        app = create_app(ledger, load_prices(args.prices), local_tokens, key, offline=args.offline)
        web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
    else:
        if args.command == "adjust":
            ledger.adjust(args.bucket, args.amount, temporary=args.temporary)
        print(json.dumps(ledger.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
