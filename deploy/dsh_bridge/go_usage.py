"""Read quota windows for the existing OpenCode Go inference account."""

import time
from datetime import datetime

from aiohttp import BasicAuth, ClientConnectorError, ClientError, ClientSession, ClientTimeout

GO_USAGE_URL = "https://opencode.ai/inference/go/v1/usage"
WINDOWS = ("rolling", "weekly", "monthly")


def unknown(reason):
    return {"status": "unknown", "source": "opencode", "reason": reason}


def normalize_usage(payload, now=None):
    go = payload.get("go") if isinstance(payload, dict) else None
    if not isinstance(go, dict):
        return unknown("未取得 Go 额度窗口；请检查 OpenCode Console 工作区登录和服务版本")
    windows = {}
    for name in WINDOWS:
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


def normalize_key_usage(payload):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return unknown("OpenCode 未返回此 Go Key 的额度窗口")
    windows = {}
    for name in WINDOWS:
        window = usage.get(name)
        if not isinstance(window, dict) or window.get("status") not in ("ok", "rate-limited"):
            return unknown("OpenCode 未返回完整的 Go 额度窗口")
        raw_reset = window.get("resetsAt")
        try:
            if not isinstance(raw_reset, str):
                raise ValueError
            reset = datetime.fromisoformat(raw_reset)
            if reset.tzinfo is None:
                raise ValueError
            reset_ms = int(reset.timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            return unknown("OpenCode 返回的 Go 重置时间格式不受支持")
        windows[name] = {"percent": window.get("percent"), "limited": window["status"] == "rate-limited",
                         "resetsAt": reset_ms}
    return normalize_usage({"go": windows})


async def read_go_key_usage(key):
    if not key:
        return unknown("尚未配置 OpenCode Go API Key")
    try:
        async with ClientSession(timeout=ClientTimeout(total=4), trust_env=False) as session:
            async with session.get(GO_USAGE_URL, allow_redirects=False,
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                             "User-Agent": "FishHome-DSH/0.2"}) as response:
                if response.status == 200:
                    return normalize_key_usage(await response.json(content_type=None))
                if response.status in (401, 403):
                    return unknown("官方额度接口拒绝此 Go Key，请检查 Key 和订阅工作区")
                return unknown("OpenCode 官方额度接口暂时不可用")
    except (ClientError, OSError, ValueError, TimeoutError):
        return unknown("无法读取 OpenCode 官方额度，请检查 ThinkBook 网络")


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
