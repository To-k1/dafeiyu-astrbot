"""Loopback gateway for metered DeepSeek Flash and explicit read-only tools."""

import argparse
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .fish_cost_ledger import BOUNDED_OPENAI_KIND, BUCKETS, CostLimit, Ledger, yuan
from .fish_cost_prompt import openai_input_quote
from plugin.astrbot_plugin_bili_activity.cost_window import peak_end


OFFICIAL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
CONTEXT_LIMIT = 1_048_576
OUTPUT_LIMIT = 8192
REQUEST_TIMEOUT = 120
PRICE_FILE = Path(__file__).with_name("fish_cost_prices.json")
ENDPOINTS = {"openai": "/v1/chat/completions", "anthropic": "/anthropic/v1/messages",
             "search": "/v1/tools/search", "fetch": "/v1/tools/fetch"}
INLINE_IMAGE = re.compile(r"data:image/(?:jpeg|png|gif|webp);base64,[A-Za-z0-9+/]+={0,2}")
logger = logging.getLogger(__name__)


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
    images = 0
    for message in body["messages"]:
        blocks = message.get("content")
        if isinstance(blocks, list):
            for block in blocks:
                if kind == "openai" and message.get("role") == "user" and block.get("type") == "image_url":
                    image = block["image_url"]
                    url = image["url"]
                    images += 1
                    if (images > 4 or len(url) > 1_400_000 or not INLINE_IMAGE.fullmatch(url)
                            or image.get("detail", "auto") not in {"auto", "low", "high", "original"}):
                        raise ValueError("invalid inline image")
                    continue
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


async def stream_usage(request, upstream, kind, *, recover_openai_end=None):
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    forwarding = True
    try:
        await response.prepare(request)
    except ConnectionError:
        forwarding = False
    usage, complete, finished = {}, False, False
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
                        if recover_openai_end and any(choice.get("finish_reason")
                                                      for choice in event.get("choices") or []):
                            finished = True
                    except (ValueError, TypeError, AttributeError):
                        pass
            if forwarding:
                try:
                    await response.write(line)
                except ConnectionError:
                    # The upstream may still finish and report billable usage.
                    forwarding = False
        if not complete and finished and recover_openai_end:
            try:
                recover_openai_end(usage)
            except ValueError:
                pass
            else:
                complete = True
                if forwarding:
                    await response.write(b"\ndata: [DONE]\n\n")
        if forwarding:
            await response.write_eof()
    except (ClientError, ConnectionError, TimeoutError, ValueError):
        if not complete:
            return response, None
    return response, usage if complete else None


def error(status, code, message):
    return web.json_response({"type": "error", "error": {"type": code, "code": code, "message": message}},
                             status=status)


def create_app(ledger, prices, tokens_by_bucket, api_key, *, upstream_base=OFFICIAL, offline=False, go_files=None,
               use_request_bounds=False):
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
        return web.json_response({**ledger.snapshot(), "flash_request_reserve": yuan(reserve_model(OUTPUT_LIMIT)),
                                  "request_bounds": use_request_bounds and not ledger.has_request_bound_overrun()})

    def reserve_model(output, input_limit=CONTEXT_LIMIT):
        return max(r["miss"] * input_limit + r["output"] * output
                   for r in (prices["peak"], prices["off_peak"]))

    async def forward(request):
        bucket, kind = request.match_info["bucket"], request.match_info["kind"]
        if bucket not in BUCKETS or not authorized(request, bucket):
            return error(401, "authentication_error", "账本凭据无效")
        if offline:
            return error(503, "fish_cost_offline", "计费入口可查询余额，请先在本机配置 DeepSeek API Key。")
        tool = kind in prices["tools"]
        quote, reservation_kind = None, kind
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request must be a JSON object")
            if tool:
                maximum = prices["tools"][kind]
            else:
                output = normalize_model(body, kind)
                if kind == "openai":
                    quote = openai_input_quote(body)
                if (use_request_bounds and quote is not None and quote < CONTEXT_LIMIT
                        and not ledger.has_request_bound_overrun()):
                    reservation_kind = BOUNDED_OPENAI_KIND
                    maximum = reserve_model(output, quote)
                else:
                    maximum = reserve_model(output)
        except (ValueError, KeyError, TypeError, AttributeError):
            return error(400, "invalid_request_error", "仅支持指定的 Flash 文本或独立搜索抓取请求")

        now = ledger.clock()
        try:
            ident = ledger.reserve(bucket, reservation_kind, maximum, now)
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
                    actual = sum(prices[band][key] * value for key, value in counts.items())
                    bound_exceeded = quote is not None and (counts["miss"] + counts["hit"] > quote
                                                            or counts["output"] > output or actual > maximum)
                    ledger.settle(ident, actual, bound_exceeded=bound_exceeded)
                    if bound_exceeded:
                        logger.error("Flash usage exceeded request quote (bucket=%s, input=%s/%s, output=%s/%s)",
                                     bucket, counts["miss"] + counts["hit"], quote, counts["output"], output)
                return response
        except (ClientError, TimeoutError, ValueError):
            return error(502, "upstream_error", "DeepSeek 结果未确认，已保留预留额")

    app.cleanup_ctx.append(session_context)
    app.router.add_get("/balance", balance)
    app.router.add_post("/{kind:openai}/{bucket}/v1/chat/completions", forward)
    app.router.add_post("/{kind:anthropic}/{bucket}/v1/messages", forward)
    app.router.add_post("/tools/{bucket}/{kind:search|fetch}", forward)
    if go_files:
        from deploy.dsh_bridge.go_file_gateway import mount_go_files
        mount_go_files(app, ledger.path, session_key=session_key, **go_files)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=6197)
    serve.add_argument("--prices", type=Path, default=PRICE_FILE)
    serve.add_argument("--offline", action="store_true", help="Serve balances without forwarding paid requests")
    serve.add_argument("--request-bounds", action="store_true",
                       help="Use the Flash 1017 request quote after comparing against hosted usage")
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
        go_key = os.environ.get("FISH_GO_FILE_KEY")
        go_files = ({"key": go_key, "credentials": {
            bucket: os.environ["FISH_GO_FILE_" + bucket.upper()] for bucket in BUCKETS}} if go_key else None)
        app = create_app(ledger, load_prices(args.prices), local_tokens, key, offline=args.offline, go_files=go_files,
                         use_request_bounds=args.request_bounds)
        web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
    else:
        if args.command == "adjust":
            ledger.adjust(args.bucket, args.amount, temporary=args.temporary)
        print(json.dumps(ledger.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
