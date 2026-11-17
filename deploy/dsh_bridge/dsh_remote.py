"""Local authenticated transport for Bridge 1.0.0 and DSH 0.2.2-rc.1."""

import asyncio
from uuid import uuid4

import aiohttp

from .dsh_client import DshClient, DshError


class RemoteDshClient(DshClient):
    """Reuse the Bridge task flow with DSH's authenticated Remote API."""

    def __init__(self, launch_token: str = "", port: int = 59934, *, session_cookie: str | None = None,
                 compute_enabled: bool = False, go_file_token: str = "", go_usage_token: str = ""):
        super().__init__({"dsh_host": "127.0.0.1", "dsh_port": port})
        self._launch_token = launch_token
        self._cookie = session_cookie
        self.compute_enabled = compute_enabled
        self.go_file_token = go_file_token
        self.go_usage_token = go_usage_token
        self.model_route = "go" if go_file_token else "deepseek"

    async def go_receipts(self, session_id):
        if not self.go_file_token:
            raise DshError("Go 文件回执入口未启用")
        bucket = "interaction" if self.port_override == 59935 else "autonomous"
        session = await self._session()
        try:
            async with session.get(f"http://127.0.0.1:6197/go/{bucket}/receipts/{session_id}",
                    headers={"Authorization": "Bearer " + self.go_file_token},
                    timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise DshError("Go 文件用量回执未确认") from None

    async def go_quota_available(self):
        if not self.go_usage_token:
            return False
        try:
            session = await self._session()
            async with session.get("http://127.0.0.1:6196/api/worker/go-usage",
                    headers={"Authorization": "Bearer " + self.go_usage_token},
                    timeout=aiohttp.ClientTimeout(total=6), allow_redirects=False) as response:
                return response.status == 200 and (await response.json()).get("status") == "available"
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False

    async def discover(self, force=False, progress=None):
        return f"http://127.0.0.1:{self.port_override}"

    async def ping(self):
        listing = await self.call("session.list", timeout=5)
        items = listing["items"]
        return {"base": await self.discover(), "sessions": len(items),
                "running": sum(bool(item.get("running")) for item in items)}

    async def run_prompt(self, *args, **kwargs):
        result = await super().run_prompt(*args, **kwargs)
        history = await self.call("session.history", {"sessionId": result["sessionId"]})
        for entry in reversed(history["events"]):
            event = entry.get("event") or entry
            if event.get("type") == "turn/end":
                reason = event.get("data", {}).get("reason", {}).get("kind")
                if reason == "completed":
                    return result
                raise DshError(f"DSH 回合结束状态为 {reason or 'unknown'}")
        raise DshError("DSH 没有返回回合终态")

    async def _authenticate(self, base, session):
        async with self._discovery_lock:
            if self._cookie is not None:
                return self._cookie
            if not self._launch_token:
                raise DshError("DSH 本机认证未配置")
            async with session.get(
                f"{base}/connect",
                params={"token": self._launch_token, "interactions": "none"},
                allow_redirects=False,
            ) as response:
                if response.status != 302 or len(response.cookies) != 1:
                    raise DshError(f"DSH 认证失败（HTTP {response.status}）")
                name, cookie = next(iter(response.cookies.items()))
                self._cookie = f"{name}={cookie.value}"
            return self._cookie

    async def call(self, method, payload=None, timeout=30.0):
        if method != "workspaceFiles.read":
            return await self._call_once(method, payload, timeout)

        request = dict(payload or {})
        offset = 1
        pages = []
        first = None
        async with asyncio.timeout(timeout):
            while True:
                page = await self._call_once(method, {**request, "_offset": offset}, timeout)
                if (not isinstance(page, dict) or page.get("offset") != offset
                        or not isinstance(page.get("text"), str)
                        or type(page.get("lines")) is not int or page["lines"] < 0
                        or type(page.get("eof")) is not bool):
                    raise DshError("DSH 文件分页返回无效结果")
                if first is None:
                    first = page
                elif page.get("version") != first.get("version"):
                    raise DshError("DSH 文件读取期间发生变更")
                if page["lines"]:
                    pages.append(page["text"])
                if page["eof"]:
                    break
                if page["lines"] == 0:
                    raise DshError("DSH 文件分页没有前进")
                offset += page["lines"]
        content = "\n".join(pages)
        # The native line API omits a final newline. Its stat reports byte size,
        # allowing that single omitted byte to be restored without guessing.
        size = first.get("bytes")
        if type(size) is int and len(content.encode("utf-8")) + 1 == size:
            content += "\n"
        return {**first, "text": content, "lines": offset - 1 + page["lines"], "eof": True}

    async def _call_once(self, method, payload=None, timeout=30.0):
        request = dict(payload or {})
        if method == "session.list":
            endpoint, args = "session/list", {"_request": request}
        elif method == "session.modelCatalog":
            endpoint, args = "session/modelCatalog", {}
        elif method == "workspaceFiles.read":
            endpoint, args = "workspaceFiles/read", {"workspaceFileScopeId": request["sessionId"],
                "path": request["path"], "range": {"offset": request.get("_offset", 1), "limit": 200}}
        elif method == "session.history":
            session_id = request.pop("sessionId")
            request["address"] = {"kind": "session", "sessionId": session_id}
            endpoint, args = "session/events", {"request": request}
        elif method in {"session.create", "session.prompt", "session.cancel", "session.state",
                        "session.selectModel", "session.projections"}:
            if method == "session.prompt":
                request.setdefault("requestId", str(uuid4()))
            endpoint, args = method.replace(".", "/"), {"request": request}
        else:
            raise DshError(f"当前 DSH 版本没有对应的 Bridge 方法 {method}")

        session = await self._session()
        base = await self.discover()
        try:
            async with asyncio.timeout(timeout):
                cookie = await self._authenticate(base, session)
                body = {"type": "client-request", "rpcId": str(uuid4()),
                        "method": endpoint, "payload": {"args": args}}
                async with session.post(
                    f"{base}/api/{endpoint}", json=body, headers={"Cookie": cookie},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise DshError(f"DSH 调用 {method} 返回 HTTP {response.status}")
                    result = (await response.json(content_type=None))["result"]
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise DshError(f"DSH 调用 {method} 失败（{type(exc).__name__}）") from None

        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise DshError(f"DSH 调用 {method} 返回无效结果")
        if not result["ok"]:
            code = result.get("error", {}).get("code", "unknown")
            raise DshError(f"DSH 拒绝 {method}（{code}）")
        value = result["value"]
        if method == "session.history":
            return {**value, "events": value["records"]}
        return value
