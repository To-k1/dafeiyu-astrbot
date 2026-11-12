"""Open the ThinkBook console through the desktop's pinned SSH connection."""

import argparse
import asyncio
import base64
import hmac
import json
import re
import secrets
import shutil
import subprocess
import webbrowser
from pathlib import Path, PureWindowsPath

from aiohttp import ClientError, ClientSession, ClientTimeout, web


HERE = Path(__file__).resolve().parent
DESKTOP_PORT = 16196
MANAGER_FORWARD = 16197
THINKBOOK_PORT = 6196
MODULE_PORTS = {6185: 16185, 6099: 16099, 8767: 18767, 59934: 15934}
ROUTES = {
    "/api/ping": {"GET"}, "/api/status": {"GET"}, "/api/config": {"GET", "POST"},
    "/api/diagnostics": {"GET"}, "/api/ui/dsh": {"POST"}, "/api/archives": {"GET"},
    "/api/archives/backup": {"POST"}, "/api/archives/export": {"POST"},
    "/api/budgets": {"GET", "POST"}, "/api/provider-keys": {"GET"},
}
SERVICE_ACTION = re.compile(r"/api/services/(budget|ollama|emulator|phone|bili|dsh|dsh_commission|astrbot|napcat|room|all)/(start|stop|restart)")


def module_url(text):
    return re.sub(r"http://127\.0\.0\.1:(6185|6099|8767|59934)(?=/)",
                  lambda match: f"http://127.0.0.1:{MODULE_PORTS[int(match[1])]}", text)


def console_page():
    page = (HERE / "index.html").read_text(encoding="utf-8")
    page = page.replace("台式机试运行", "ThinkBook 远程控制")
    page = page.replace("<title>深海工作台 · FishHome</title>", "<title>ThinkBook · FishHome</title>")
    page = page.replace('<button id="start" class="primary">', '<button id="start" class="primary" disabled>')
    page = page.replace("</head>", "<style>.locations button,#backup-folder{display:none}</style></head>")
    return module_url(page)


def create_relay(client, browser_token, manager_token):
    async def handle(request):
        if request.host != f"127.0.0.1:{DESKTOP_PORT}":
            return web.Response(status=403)
        if request.path == "/" and request.method == "GET":
            return web.Response(text=console_page(), content_type="text/html", headers={
                "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; img-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'",
            })
        if request.path == "/icon.png" and request.method == "GET":
            return web.FileResponse(HERE / "fishhome.png")
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(),
                                   ("Bearer " + browser_token).encode()):
            return web.json_response({"error": "请从 ThinkBook 控制入口打开页面。"}, status=401)
        action = SERVICE_ACTION.fullmatch(request.path) if request.method == "POST" else None
        if not action and request.method not in ROUTES.get(request.path, ()):
            return web.json_response({"error": "此操作请在 ThinkBook 本机执行。"}, status=404)
        if action and action[1] in {"napcat", "all"} and action[2] in {"start", "restart"}:
            return web.json_response({"error": "QQ 正式切换前，请逐项启动其他服务。"}, status=409)
        headers = {"Host": f"127.0.0.1:{THINKBOOK_PORT}", "Authorization": f"Bearer {manager_token}"}
        if request.can_read_body:
            headers["Content-Type"] = request.content_type
        try:
            async with client.request(request.method, f"http://127.0.0.1:{MANAGER_FORWARD}{request.path}",
                                      headers=headers, data=await request.read() if request.can_read_body else None,
                                      allow_redirects=False) as response:
                body = await response.text()
                if response.status == 401:
                    return web.json_response({"error": "ThinkBook 控制台已重启，请重新打开远程入口。"}, status=502)
                return web.Response(text=module_url(body), status=response.status, content_type=response.content_type,
                                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        except (ClientError, OSError, asyncio.TimeoutError):
            return web.json_response({"error": "ThinkBook 控制台暂时不可达。"}, status=502)

    app = web.Application(client_max_size=8192)
    app.router.add_route("*", "/{path:.*}", handle)
    return app


def ssh_args():
    return [shutil.which("ssh") or "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8"]


def remote_token(root=None):
    if root:
        path = PureWindowsPath(root)
        if not path.is_absolute():
            raise ValueError("ThinkBook 路径须为绝对路径。")
        select = "$root='" + str(path).replace("'", "''") + "';"
    else:
        select = (
            "$roots=@(Get-Item -LiteralPath 'D:\\FishHome';Get-ChildItem -LiteralPath 'D:\\FishHome' -Directory);"
            "$roots=@($roots|Where-Object {Test-Path (Join-Path $_.FullName 'dev\\FishHome.exe')});"
            "if($roots.Count -ne 1){exit 3};$root=$roots[0].FullName;"
        )
    read_token = """import re
from pathlib import Path
import psutil
root = Path.cwd()
pid = next(row.pid for row in psutil.net_connections("tcp")
           if row.status == "LISTEN" and row.laddr and row.laddr.port == 6196)
proc = psutil.Process(pid)
args = proc.cmdline()
assert "deploy.desktop_manager.manager" in args and Path(args[args.index("--dev")+1]).resolve() == (root/"dev").resolve()
token = proc.environ().get("FISH_MANAGER_TOKEN", "")
assert re.fullmatch(r"[a-zA-Z0-9_-]{40,80}", token)
print(token)
"""
    command = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" + select
               + "Set-Location -LiteralPath $root;\n@'\n" + read_token
               + "'@ | & (Join-Path $root 'dev\\astrbot-4.29.4\\.venv\\Scripts\\python.exe') -B -;exit $LASTEXITCODE")
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = subprocess.run([*ssh_args(), "fishbook", "powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=20)
    token = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[a-zA-Z0-9_-]{40,80}", token):
        raise RuntimeError("无法读取 ThinkBook 控制台。请确认 FishHomeOps 已打开 FishHome.exe，且 fishbook 公钥登录有效。")
    return token


async def open_console(root=None, *, check=False):
    manager_token = await asyncio.to_thread(remote_token, root)
    forwards = [(MANAGER_FORWARD, THINKBOOK_PORT), *((local, remote) for remote, local in MODULE_PORTS.items())]
    options = [part for local, remote in forwards for part in ("-L", f"127.0.0.1:{local}:127.0.0.1:{remote}")]
    tunnel = await asyncio.create_subprocess_exec(
        *ssh_args(), "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        *options, "fishbook", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    runner = None
    try:
        async with ClientSession(timeout=ClientTimeout(total=50, sock_connect=3)) as client:
            for _ in range(40):
                if tunnel.returncode is not None:
                    break
                try:
                    async with client.get(f"http://127.0.0.1:{MANAGER_FORWARD}/api/ping", headers={
                        "Host": f"127.0.0.1:{THINKBOOK_PORT}", "Authorization": "Bearer " + manager_token,
                    }) as response:
                        if response.status == 200 and await response.text() == "FishHome":
                            break
                except (ClientError, OSError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(.2)
            else:
                raise RuntimeError("ThinkBook 控制台转发不可用。")
            if tunnel.returncode is not None:
                raise RuntimeError("SSH 转发无法启动，请确认本机远程控制端口未占用。")
            browser_token = secrets.token_urlsafe(32)
            runner = web.AppRunner(create_relay(client, browser_token, manager_token), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", DESKTOP_PORT).start()
            if check:
                async with client.get(f"http://127.0.0.1:{DESKTOP_PORT}/api/status",
                                      headers={"Authorization": "Bearer " + browser_token}) as response:
                    response.raise_for_status()
                    status = await response.json()
                print(json.dumps({row["id"]: row["state"] for row in status["services"]}, ensure_ascii=False))
            else:
                webbrowser.open(f"http://127.0.0.1:{DESKTOP_PORT}/#{browser_token}")
                await tunnel.wait()
    finally:
        if runner:
            await runner.cleanup()
        if tunnel.returncode is None:
            tunnel.terminate()
            await tunnel.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="ThinkBook 安装目录，默认在 D:\\FishHome 下查找。")
    parser.add_argument("--check", action="store_true", help="只验证台式机到 ThinkBook 的控制通路。")
    args = parser.parse_args()
    try:
        asyncio.run(open_console(args.root, check=args.check))
    except (ClientError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
