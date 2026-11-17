"""Expose the dedicated Windows broker to a single DSH task."""

import base64
import json

from aiohttp import ClientError, ClientSession, ClientTimeout
from mcp.types import ImageContent, TextContent

from deploy.dsh_bridge.public_video import public_mcp, render_tool_preset
from deploy.dsh_bridge.windows_desktop import PORT


DESKTOP_PRESET = "fishhome-windows-desktop"
DESKTOP_URL = f"http://127.0.0.1:{PORT}/desktop"


async def desktop_call(token, op, name="", text=""):
    try:
        timeout = 75 if op == "app_install" else 18
        async with ClientSession(timeout=ClientTimeout(total=timeout), trust_env=False) as session:
            async with session.post(DESKTOP_URL, headers={"Authorization": "Bearer " + token},
                json={"op": op, "name": name, "text": text}, allow_redirects=False) as result:
                if result.status == 401:
                    raise ValueError("专用 Windows 会话未配对")
                body = await result.json()
                if result.status != 200 or "error" in body:
                    raise ValueError(body.get("error", "桌面操作失败"))
                return body
    except (ClientError, OSError, TimeoutError):
        raise ValueError("专用 Windows 桌面当前不可用") from None


def desktop_server(evidence, desktop_token):
    server = public_mcp("FishHome Windows desktop")

    def record(action, result):
        with evidence.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"action": action, **result}, ensure_ascii=False) + "\n")

    @server.tool(structured_output=False)
    async def windows_notepad(op: str, name: str = "", text: str = "") -> list[TextContent | ImageContent]:
        """Inspect memory or registered apps; open, write/save, capture, or close a named test .txt file in Notepad.

        Allowed operations: memory, list, open, write, capture, close. Write replaces the text and verifies the saved file.
        A close result of kept_open is pending; do not force close or discard unsaved changes.
        """
        result = await desktop_call(desktop_token, op, name, text)
        raw_image = result.pop("png", "")
        outputs = [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        if raw_image:
            if len(raw_image) > 4_000_000 or not base64.b64decode(raw_image, validate=True).startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("桌面截图无效")
            outputs.append(ImageContent(type="image", data=raw_image, mimeType="image/png"))
        record(op, result)
        return outputs

    @server.tool(structured_output=False)
    async def windows_apps(op: str, app_id: str = "") -> list[TextContent]:
        """List, install, launch, or request normal close for a registered personal app.

        Allowed operations: list, install, launch, close. Only use IDs from the list.
        Install unpacks the app's registered local ZIP into its private software directory.
        An installed app is left in place; launch is a separate operation.
        kept_open means the app still needs attention. Leave dialogs and unsaved changes to the user.
        """
        if op not in {"list", "install", "launch", "close"}:
            raise ValueError("只支持列出、安装、启动或正常关闭个人软件")
        action = "app_" + op
        result = await desktop_call(desktop_token, action, app_id)
        record(action, result)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    return server


def render_desktop_preset(url, token):
    return render_tool_preset(DESKTOP_PRESET, {"serverName": "fishhome_windows_desktop",
        "transport": "streamable-http", "url": url, "headers": {"Authorization": "Bearer " + token}},
        "Operate via windows_notepad or windows_apps on the dedicated Windows account. Record actual receipts. "
        "Do not claim the operation happened without the tool result or force close unsaved work.")
