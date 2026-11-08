"""Local authenticated transport for Bridge 1.0.0 and DSH 0.2.2-rc.1."""

import asyncio
from uuid import uuid4

import aiohttp

from .dsh_client import DshClient, DshError


class RemoteDshClient(DshClient):
    """Reuse the Bridge task flow with DSH's authenticated Remote API."""

    def __init__(self, launch_token: str, port: int = 59934):
        super().__init__({"dsh_host": "127.0.0.1", "dsh_port": port})
        self._launch_token = launch_token
        self._cookie = None

    async def discover(self, force=False, progress=None):
        return f"http://127.0.0.1:{self.port_override}"

    async def _authenticate(self, base, session):
        async with self._discovery_lock:
            if self._cookie is not None:
                return self._cookie
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
        request = dict(payload or {})
        if method == "session.list":
            endpoint, args = "session/list", {"_request": request}
        elif method == "session.modelCatalog":
            endpoint, args = "session/modelCatalog", {}
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
