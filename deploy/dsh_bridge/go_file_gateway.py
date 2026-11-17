"""Go relay for persistent DSH file sessions, separate from CNY accounting."""

from collections import OrderedDict
from contextlib import closing
import hmac
import re
import sqlite3

from aiohttp import web

from deploy.dsh_bridge.go_profile import BASE_URL
from deploy.dsh_bridge.go_relay import GoRelay
from deploy.fish_cost import error


SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")


def mount_go_files(app, database, key, credentials, session_key, *, upstream=BASE_URL):
    if not key or set(credentials) != {"interaction", "autonomous"} or any(
            len(token) < 24 or token == key for token in credentials.values()):
        raise ValueError("Go 文件网关需要独立凭据")
    relays = OrderedDict()

    def db():
        return sqlite3.connect(database, timeout=5)

    with closing(db()) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS go_file_receipts "
            "(bucket TEXT NOT NULL, session TEXT NOT NULL, source TEXT NOT NULL, allowance TEXT NOT NULL, "
            "input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS go_receipts_session ON go_file_receipts(bucket,session)")

    def check(request, session):
        bucket = request.match_info["bucket"]
        token = credentials.get(bucket)
        if not token or not hmac.compare_digest(request.headers.get("Authorization", "").encode(),
                                                ("Bearer " + token).encode()):
            return None
        return (bucket, session) if SESSION.fullmatch(session or "") else None

    def record(bucket, session, receipt):
        with closing(db()) as connection, connection:
            connection.execute("INSERT INTO go_file_receipts VALUES (?,?,?,?,?,?)", (bucket, session,
                receipt.billing_source, str(receipt.allowance), receipt.input_tokens, receipt.output_tokens))

    async def forward(request):
        identity = check(request, request.headers.get("x-opencode-session"))
        if not identity:
            return error(401, "unauthorized", "Go 文件账户或会话参数无效")
        relay = relays.get(identity)
        if relay is None:
            if len(relays) >= 192:
                first_idle = next((item for item, candidate in relays.items() if not candidate.lock.locked()), None)
                if first_idle is None:
                    return error(503, "busy", "Go 文件网关正在处理任务")
                del relays[first_idle]
            relay = GoRelay(key, credentials[identity[0]], identity[1], upstream=upstream,
                            on_receipt=lambda receipt: record(*identity, receipt))
            relay.client = request.app[session_key]
            relays[identity] = relay
        relays.move_to_end(identity)
        return await relay.forward(request)

    async def receipts(request):
        identity = check(request, request.match_info["session"])
        if not identity:
            return error(401, "unauthorized", "无权查看 Go 文件回执")
        relay = relays.get(identity)
        with closing(db()) as connection:
            rows = connection.execute(
                "SELECT source,allowance,input_tokens,output_tokens FROM go_file_receipts "
                "WHERE bucket=? AND session=? ORDER BY rowid", identity).fetchall()
        return web.json_response({"source": "go", "limited": bool(relay and relay.status in {402, 429}),
            "needs_review": bool(relay and relay.failure and relay.status not in {402, 429}),
            "receipts": [{"source": row[0], "allowance": row[1], "input_tokens": row[2], "output_tokens": row[3]}
                         for row in rows]})

    app.router.add_post("/go/{bucket}/v1/chat/completions", forward)
    app.router.add_get("/go/{bucket}/receipts/{session}", receipts)
