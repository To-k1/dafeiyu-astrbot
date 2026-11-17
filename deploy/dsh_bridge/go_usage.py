"""Read the official OpenCode local server's Go usage projection."""

import time

from aiohttp import BasicAuth, ClientError, ClientSession, ClientTimeout


def normalize_usage(payload, now=None):
    go = payload.get("go") if isinstance(payload, dict) else None
    if not isinstance(go, dict):
        return {"status": "unknown", "source": "opencode"}
    windows = {}
    for name in ("rolling", "weekly", "monthly"):
        item = go.get(name)
        if not isinstance(item, dict):
            return {"status": "unknown", "source": "opencode"}
        percent, limited, resets = (item.get(field) for field in ("percent", "limited", "resetsAt"))
        if type(percent) not in (int, float) or not 0 <= percent <= 100 or type(limited) is not bool:
            return {"status": "unknown", "source": "opencode"}
        if type(resets) not in (int, float) or not 0 <= resets <= 10**14:
            return {"status": "unknown", "source": "opencode"}
        windows[name] = {"percent": percent, "limited": limited, "resets_at_ms": resets}
    return {"status": "limited" if any(item["limited"] for item in windows.values()) else "available",
            "source": "opencode", "checked_at": time.time() if now is None else now, "windows": windows}


async def read_go_usage(port, password):
    if not password or type(port) is not int or not 1 <= port <= 65535:
        return {"status": "unknown", "source": "opencode", "reason": "未连接 OpenCode 本机服务"}
    try:
        async with ClientSession(timeout=ClientTimeout(total=4), trust_env=False) as session:
            async with session.get(f"http://127.0.0.1:{port}/api/experimental/usage/limits",
                    auth=BasicAuth("opencode", password), allow_redirects=False) as response:
                if response.status == 200:
                    return normalize_usage(await response.json())
    except (ClientError, OSError, ValueError, TimeoutError):
        pass
    return {"status": "unknown", "source": "opencode", "reason": "本机 OpenCode 额度不可读"}
