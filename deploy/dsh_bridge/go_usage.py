"""Read the official OpenCode local server's Go usage projection."""

import time

from aiohttp import BasicAuth, ClientConnectorError, ClientError, ClientSession, ClientTimeout


def unknown(reason):
    return {"status": "unknown", "source": "opencode", "reason": reason}


def normalize_usage(payload, now=None):
    go = payload.get("go") if isinstance(payload, dict) else None
    if not isinstance(go, dict):
        return unknown("未取得 Go 额度窗口；请检查 OpenCode Console 工作区登录和服务版本")
    windows = {}
    for name in ("rolling", "weekly", "monthly"):
        item = go.get(name)
        if not isinstance(item, dict):
            return unknown("未取得完整 Go 额度窗口；请检查 OpenCode Console 工作区登录和服务版本")
        percent, limited, resets = (item.get(field) for field in ("percent", "limited", "resetsAt"))
        if type(percent) not in (int, float) or not 0 <= percent <= 100 or type(limited) is not bool:
            return unknown("OpenCode 返回的 Go 额度格式不受支持")
        if type(resets) not in (int, float) or not 0 <= resets <= 10**14:
            return unknown("OpenCode 返回的 Go 额度格式不受支持")
        windows[name] = {"percent": percent, "limited": limited, "resets_at_ms": resets}
    return {"status": "limited" if any(item["limited"] for item in windows.values()) else "available",
            "source": "opencode", "checked_at": time.time() if now is None else now, "windows": windows}


async def read_go_usage(port, password):
    if not password:
        return unknown("尚未配对 OpenCode 本机额度服务，请在控制台保存服务密码")
    if type(port) is not int or not 1 <= port <= 65535:
        return unknown("OpenCode 本机服务端口配置无效")
    try:
        async with ClientSession(timeout=ClientTimeout(total=4), trust_env=False) as session:
            async with session.get(f"http://127.0.0.1:{port}/api/experimental/usage/limits",
                    auth=BasicAuth("opencode", password), allow_redirects=False) as response:
                if response.status == 200:
                    return normalize_usage(await response.json())
                if response.status in (401, 403):
                    return unknown("OpenCode 本机服务认证失败，请检查配对密码")
                if response.status == 404:
                    return unknown("OpenCode 额度接口或工作区不可用，请检查服务版本和登录")
                return unknown("OpenCode 本机服务未能提供额度，请检查服务和工作区登录")
    except ClientConnectorError:
        return unknown("无法连接 OpenCode 本机服务，请检查服务是否启动及端口")
    except (ClientError, OSError, ValueError, TimeoutError):
        return unknown("OpenCode 本机额度读取失败，请检查服务和工作区登录")
