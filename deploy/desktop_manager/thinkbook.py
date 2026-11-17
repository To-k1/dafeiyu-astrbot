"""Open the ThinkBook console through the desktop's pinned SSH connection."""

import argparse
import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import webbrowser
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile

from aiohttp import ClientError, ClientSession, ClientTimeout, web
import psutil

from scripts.m6_data import sync_room
from scripts.mock_backup import checksum, staged_directory


HERE = Path(__file__).resolve().parent
DESKTOP_PORT = 16196
MANAGER_FORWARD = 16197
THINKBOOK_PORT = 6196
OBSERVATORY_PORT = 18767
OBSERVATORY_FORWARD = 18768
THINKBOOK_OBSERVATORY = 8767
MODULE_PORTS = {6185: 16185, 6099: 16099, 59934: 15934}
ROUTES = {
    "/api/ping": {"GET"}, "/api/status": {"GET"}, "/api/config": {"GET", "POST"},
    "/api/diagnostics": {"GET"}, "/api/ui/dsh": {"POST"}, "/api/archives": {"GET"},
    "/api/archives/backup": {"POST"}, "/api/archives/export": {"POST"},
    "/api/budgets": {"GET", "POST"}, "/api/provider-keys": {"GET"},
    "/api/dsh-team": {"GET"},
    "/api/dsh-team/cancel": {"POST"},
}
SERVICE_ACTION = re.compile(r"/api/services/(budget|ollama|emulator|phone|bili|compute|desktop|dsh|dsh_commission|astrbot|napcat|room|all)/(start|stop|restart)")


def module_url(text):
    ports = MODULE_PORTS | {THINKBOOK_OBSERVATORY: OBSERVATORY_PORT}
    return re.sub(r"http://127\.0\.0\.1:(6185|6099|59934|8767)(?=/)",
                  lambda match: f"http://127.0.0.1:{ports[int(match[1])]}", text)


def create_observatory_relay(client):
    from deploy.memory_observatory.server import ASSETS

    readable = set(ASSETS) | {"/api/health", "/api/room", "/api/snapshot", "/api/example"}
    queries = {"/api/room/refresh", "/api/livingmemory/refresh", "/api/project"}

    async def handle(request):
        local = f"127.0.0.1:{OBSERVATORY_PORT}"
        origin = request.headers.get("Origin")
        if request.host != local or origin not in {None, f"http://{local}"}:
            return web.Response(status=403)
        if request.path not in (readable if request.method == "GET" else queries):
            return web.Response(status=404)
        headers = {"Host": f"127.0.0.1:{THINKBOOK_OBSERVATORY}"}
        if request.method == "POST":
            if request.headers.get("X-Observatory") != "local" or request.content_type != "application/json":
                return web.Response(status=403)
            headers.update({"X-Observatory": "local", "Content-Type": "application/json"})
        try:
            async with client.request(
                request.method, f"http://127.0.0.1:{OBSERVATORY_FORWARD}{request.path}",
                headers=headers, data=await request.read() if request.can_read_body else None,
                allow_redirects=False,
            ) as response:
                forwarded = {name: response.headers[name] for name in (
                    "Content-Type", "Cache-Control", "Content-Security-Policy", "X-Content-Type-Options",
                    "Referrer-Policy", "X-Frame-Options",
                ) if name in response.headers}
                return web.Response(body=await response.read(), status=response.status, headers=forwarded)
        except (ClientError, OSError, asyncio.TimeoutError):
            return web.json_response({"error": "ThinkBook 房间与记忆星海暂时不可达"}, status=502)

    app = web.Application()
    for method in ("GET", "POST"):
        app.router.add_route(method, "/{path:.*}", handle)
    return app


def production_qq_marker():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "FishHome/manager/thinkbook-qq-primary"


def qq_control_ready():
    if not production_qq_marker().is_file():
        return False
    if any(row.laddr and row.laddr.port == 6099 and row.status == psutil.CONN_LISTEN
           for row in psutil.net_connections("tcp")):
        return False
    desktop_cat = Path(__file__).resolve().parents[3] / "dev/napcat-4.18.38/node.exe"
    return not any(row.info["exe"] and Path(row.info["exe"]) == desktop_cat
                   for row in psutil.process_iter(["exe"]))


def console_page(qq_ready=False):
    page = (HERE / "index.html").read_text(encoding="utf-8")
    page = page.replace("台式机试运行", "ThinkBook 远程控制")
    page = page.replace("<title>深海工作台 · FishHome</title>", "<title>ThinkBook · FishHome</title>")
    if not qq_ready:
        page = page.replace('<button id="start" class="primary">', '<button id="start" class="primary" disabled>')
    page = page.replace("</head>", "<style>.locations button,#backup-folder,#recovery,#recovery-hint{display:none}</style></head>")
    return module_url(page)


def create_relay(client, browser_token, manager_token):
    async def handle(request):
        if request.host != f"127.0.0.1:{DESKTOP_PORT}":
            return web.Response(status=403)
        if request.path == "/" and request.method == "GET":
            return web.Response(text=console_page(await asyncio.to_thread(qq_control_ready)), content_type="text/html", headers={
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
            if not await asyncio.to_thread(qq_control_ready):
                return web.json_response({"error": "请确认 ThinkBook 是正式 QQ 主机，且台式机的 NapCat 已停止。"}, status=409)
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


def select_root(root=None):
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
    return select


def sync_room_from_thinkbook(destination):
    def fetch():
        script = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';$env:PYTHONUTF8='1';"
                  + select_root() + "Set-Location -LiteralPath (Join-Path $root 'repo');"
                  "& (Join-Path $root 'dev\\astrbot-4.29.4\\.venv\\Scripts\\python.exe') -B -m scripts.fish_m6 "
                  "room-bundle --astrbot-root (Join-Path $root 'dev\\astrbot-4.29.4');exit $LASTEXITCODE")
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run([*ssh_args(), "fishbook", "powershell.exe", "-NoProfile", "-NonInteractive",
                                 "-EncodedCommand", encoded], capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
            raise OSError("ThinkBook 房间快照读取失败")
        return json.loads(result.stdout)

    return sync_room(destination, fetch)


def export_from_thinkbook(destination):
    job = 'fishhome-memory-' + uuid4().hex
    names = {'memory.jsonl', 'projects.jsonl', 'manifest.json', 'README.md', 'INDEX.md'}
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    def remote(script):
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        return subprocess.run([*ssh_args(), 'fishbook', 'powershell.exe', '-NoProfile', '-NonInteractive',
                               '-EncodedCommand', encoded], capture_output=True, timeout=120, creationflags=flags)

    if destination.exists():
        raise FileExistsError(destination)
    try:
        result = remote("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';$env:PYTHONUTF8='1';"
                        + select_root() + f"""
$dir=Join-Path $HOME '{job}'
$zip=Join-Path $HOME '{job}.zip'
try {{
    Set-Location -LiteralPath (Join-Path $root 'repo')
    $astro=Join-Path $root 'dev\\astrbot-4.29.4'
    & (Join-Path $astro '.venv\\Scripts\\python.exe') -B -m scripts.fish_m6 export-memory --astrbot-root $astro --destination $dir | Out-Null
    if ($LASTEXITCODE) {{ exit $LASTEXITCODE }}
    Compress-Archive -Path (Join-Path $dir '*') -DestinationPath $zip
    (Get-FileHash -LiteralPath $zip).Hash
}} finally {{
    if (Test-Path -LiteralPath $dir) {{
        Get-ChildItem -LiteralPath $dir -File | Remove-Item
        Remove-Item -LiteralPath $dir
    }}
}}
""")
        digest = result.stdout.decode('ascii', errors='replace').strip().lower()
        if result.returncode or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise RuntimeError('ThinkBook 可读记忆导出失败')
        with TemporaryDirectory(prefix=job) as temp:
            archive = Path(temp) / 'export.zip'
            copied = subprocess.run([shutil.which('scp') or 'scp', '-q', '-o', 'BatchMode=yes', '-o',
                                     'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
                                     f'fishbook:{job}.zip', str(archive)],
                                    capture_output=True, timeout=120, creationflags=flags)
            if copied.returncode or checksum(archive) != digest:
                raise RuntimeError('ThinkBook 记忆导出传输校验失败')
            with ZipFile(archive) as bundle:
                if len(bundle.namelist()) != len(names) or set(bundle.namelist()) != names or bundle.testzip():
                    raise ValueError('ThinkBook 记忆导出文件不完整')
                manifest = json.loads(bundle.read('manifest.json'))
                if manifest.get('format') != 'fishhome-readable-v1' or manifest.get('private_chat') is not False:
                    raise ValueError('ThinkBook 记忆导出范围不匹配')
                with staged_directory(destination) as stage:
                    bundle.extractall(stage)
        return destination
    finally:
        try:
            remote(f"Remove-Item -LiteralPath (Join-Path $HOME '{job}.zip') -ErrorAction SilentlyContinue")
        except (OSError, subprocess.SubprocessError):
            pass


def remote_token(root=None):
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
    command = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" + select_root(root)
               + "Set-Location -LiteralPath $root;\n@'\n" + read_token
               + "'@ | & (Join-Path $root 'dev\\astrbot-4.29.4\\.venv\\Scripts\\python.exe') -B -;exit $LASTEXITCODE")
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = subprocess.run([*ssh_args(), "fishbook", "powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=20)
    token = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[a-zA-Z0-9_-]{40,80}", token):
        raise RuntimeError("无法读取 ThinkBook 控制台。请确认 FishHomeOps 已打开 FishHome.exe，且 fishbook 公钥登录有效。")
    return token


async def open_console(root=None, *, check=False, tray_parent=None):
    manager_token = await asyncio.to_thread(remote_token, root)
    forwards = [(MANAGER_FORWARD, THINKBOOK_PORT), (OBSERVATORY_FORWARD, THINKBOOK_OBSERVATORY),
                *((local, remote) for remote, local in MODULE_PORTS.items())]
    options = [part for local, remote in forwards for part in ("-L", f"127.0.0.1:{local}:127.0.0.1:{remote}")]
    tunnel = await asyncio.create_subprocess_exec(
        *ssh_args(), "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        *options, "fishbook", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    runners = []
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
            runners.append(runner)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", DESKTOP_PORT).start()
            observatory = web.AppRunner(create_observatory_relay(client), access_log=None)
            runners.append(observatory)
            await observatory.setup()
            await web.TCPSite(observatory, "127.0.0.1", OBSERVATORY_PORT).start()
            if check:
                async with client.get(f"http://127.0.0.1:{DESKTOP_PORT}/api/status",
                                      headers={"Authorization": "Bearer " + browser_token}) as response:
                    response.raise_for_status()
                    status = await response.json()
                print(json.dumps({row["id"]: row["state"] for row in status["services"]}, ensure_ascii=False))
            elif tray_parent is not None:
                import win32event

                close_event = win32event.OpenEvent(0x00100000, False, rf"Local\FishHome.ThinkBookTray.close.{tray_parent}")
                parent = psutil.Process(tray_parent)
                parent_started = parent.create_time()
                try:
                    print(f"http://127.0.0.1:{DESKTOP_PORT}/#{browser_token}", flush=True)
                    while (tunnel.returncode is None and parent.is_running() and parent.create_time() == parent_started
                           and win32event.WaitForSingleObject(close_event, 0) == win32event.WAIT_TIMEOUT):
                        await asyncio.sleep(.35)
                finally:
                    close_event.Close()
            else:
                webbrowser.open(f"http://127.0.0.1:{DESKTOP_PORT}/#{browser_token}")
                await tunnel.wait()
    finally:
        for runner in reversed(runners):
            await runner.cleanup()
        if tunnel.returncode is None:
            tunnel.terminate()
            await tunnel.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="ThinkBook 安装目录，默认在 D:\\FishHome 下查找。")
    parser.add_argument("--check", action="store_true", help="只验证台式机到 ThinkBook 的控制通路。")
    parser.add_argument("--tray-parent", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        asyncio.run(open_console(args.root, check=args.check, tray_parent=args.tray_parent))
    except (ClientError, OSError, ValueError, RuntimeError, psutil.Error, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
