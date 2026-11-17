"""Local broker for registered applications in the FishHomeDesktop session."""

import argparse
import ctypes as c
import hmac
import json
import os
import re
import shutil
import secrets
import stat
import subprocess
import sys
import time
from threading import Thread
from ctypes import wintypes as w
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile


ACCOUNT = "FishHomeDesktop"
PORT = 59937
NOTE = re.compile(r"[A-Za-z0-9_-]{1,48}\.txt")
APP_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")
APP_MANIFEST = "personal-apps.json"
APP_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,90}\.zip", re.IGNORECASE)


def load_personal_apps(manifest):
    if not manifest.is_file():
        return {}
    with manifest.open(encoding="utf-8-sig") as stream:
        apps = json.load(stream)
    if not isinstance(apps, dict):
        raise ValueError("个人软件配置必须是软件 ID 到相对 EXE 路径的对象")
    for ident, spec in apps.items():
        if not APP_ID.fullmatch(ident) or ident == "notepad" or PureWindowsPath(ident).is_reserved():
            raise ValueError("个人软件配置包含无效的软件 ID 或路径")
        personal_app_spec(ident, spec)
    return apps


def invalid_path_part(part):
    return (part in {"", ".", ".."} or PureWindowsPath(part).is_reserved()
            or re.search(r'[:<>|?*"\x00-\x1f]', part) or part.endswith((" ", ".")))


def app_path_parts(executable):
    if not isinstance(executable, str):
        raise ValueError("个人软件配置包含无效的路径")
    path = PureWindowsPath(executable)
    if (path.drive or path.root or path.suffix.casefold() != ".exe"
            or any(invalid_path_part(part) for part in path.parts)):
        raise ValueError("个人软件须使用专用软件目录内的相对 EXE 路径")
    return path.parts


def personal_app_spec(ident, spec):
    if isinstance(spec, str):
        app_path_parts(spec)
        return spec, None
    if not isinstance(spec, dict) or set(spec) != {"executable", "archive"}:
        raise ValueError("软件安装项需要登记 executable 与 archive")
    executable, archive = spec["executable"], spec["archive"]
    parts = app_path_parts(executable)
    if (len(parts) < 2 or parts[0] != ident or not isinstance(archive, str)
            or not APP_ARCHIVE.fullmatch(archive) or PureWindowsPath(archive).is_reserved()):
        raise ValueError("需登记 packages 下的 ZIP 和以软件 ID 为首段的 EXE 路径")
    return executable, archive


def personal_executable(root, relative):
    if root.resolve(strict=True) != root:
        raise ValueError("个人软件目录不能经由链接到达")
    path = root
    for part in (None, *app_path_parts(relative)):
        if part is not None:
            path /= part
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("个人软件路径不能使用链接或目录连接")
        if part is None and not stat.S_ISDIR(info.st_mode):
            raise ValueError("个人软件目录不可用")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("个人软件必须是专用目录内的独立可执行文件")
    return path


def safe_package_members(package):
    seen = set()
    for entry in package.infolist():
        name = entry.orig_filename
        folder = entry.is_dir()
        parts = (name[:-1] if folder else name).split("/")
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if ("\\" in name or any(invalid_path_part(part) for part in parts)
                or kind not in {0, stat.S_IFDIR if folder else stat.S_IFREG}
                or entry.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("软件 ZIP 包含无效路径、链接或特殊文件")
        identity = tuple(part.casefold() for part in parts)
        if identity in seen:
            raise ValueError("软件 ZIP 包含重复路径")
        seen.add(identity)
        yield entry, parts


def install_personal_app(apps_root, packages_root, ident, executable, archive):
    if apps_root.resolve() != apps_root:
        raise ValueError("个人软件下载包和安装目录不能使用链接")
    apps_root.mkdir(exist_ok=True)
    destination = apps_root / ident
    if os.path.lexists(destination):
        personal_executable(apps_root, executable)
        return "already_installed"
    try:
        if packages_root.resolve(strict=True) != packages_root:
            raise ValueError("个人软件下载包和安装目录不能使用链接")
        source = packages_root / archive
        info = source.lstat()
    except FileNotFoundError as exc:
        raise ValueError("找不到已登记的软件 ZIP") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("登记的 ZIP 必须是独立的普通文件")

    try:
        with ZipFile(source) as package:
            members = list(safe_package_members(package))
            with TemporaryDirectory(prefix=".install-", dir=apps_root) as scratch:
                stage = Path(scratch).resolve()
                if stage.parent != apps_root:
                    raise ValueError("安装暂存目录超出个人软件目录")
                prepared = stage / ident
                prepared.mkdir()
                for entry, parts in members:
                    target = prepared.joinpath(*parts)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with package.open(entry) as content, target.open("xb") as output:
                            shutil.copyfileobj(content, output)
                        info = target.lstat()
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                            raise ValueError("解包结果不是普通文件")
                try:
                    personal_executable(stage, executable)
                except FileNotFoundError as exc:
                    raise ValueError("软件 ZIP 缺少登记的 EXE") from exc
                if os.path.lexists(destination):
                    raise ValueError("个人软件目录已存在，保留原安装")
                prepared.rename(destination)
    except (BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("无法读取登记的软件 ZIP") from exc
    return "installed"


class Memory(c.Structure):
    _fields_ = [("length", w.DWORD), ("load", w.DWORD), ("total", c.c_ulonglong),
                ("available", c.c_ulonglong), ("page", c.c_ulonglong), ("page_available", c.c_ulonglong),
                ("virtual", c.c_ulonglong), ("virtual_available", c.c_ulonglong), ("extended", c.c_ulonglong)]


class WindowsNote:
    def __init__(self):
        self.user = c.WinDLL("user32", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindow.argtypes = (w.HWND, w.UINT)
        self.user.GetWindow.restype = w.HWND
        self.user.OpenInputDesktop.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        self.user.OpenInputDesktop.restype = w.HANDLE
        self.user.CloseDesktop.argtypes = (w.HANDLE,)
        for name in ("IsWindow", "IsWindowVisible", "SetForegroundWindow", "ShowWindow"):
            getattr(self.user, name).argtypes = (w.HWND, c.c_int) if name == "ShowWindow" else (w.HWND,)
        self.user.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        self.user.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
        self.user.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, c.c_int)
        self.user.GetWindowRect.argtypes = (w.HWND, c.POINTER(w.RECT))
        self.kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = (w.HANDLE,)
        self.kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
        self.user.GetUserObjectInformationW.argtypes = (w.HANDLE, c.c_int, c.c_void_p,
                                                        w.DWORD, c.POINTER(w.DWORD))
        self.system = Path(os.environ["SystemRoot"]) / "System32"
        self.powershell = self.system / "WindowsPowerShell/v1.0/powershell.exe"
        self.notepad = self.system / "notepad.exe"

    def require_desktop(self):
        desktop = self.user.OpenInputDesktop(0, False, 1)
        if not desktop:
            raise ValueError("桌面未登录、锁定或不可用")
        try:
            name, needed = c.create_unicode_buffer(48), w.DWORD()
            if not self.user.GetUserObjectInformationW(desktop, 2, name, c.sizeof(name), c.byref(needed)):
                raise ValueError("无法确认交互桌面")
            if name.value.casefold() != "default":
                raise ValueError("当前输入桌面不可用")
        finally:
            self.user.CloseDesktop(desktop)

    def notepad_window(self, hwnd, filename):
        if not self.user.IsWindowVisible(hwnd):
            return False
        caption = c.create_unicode_buffer(512)
        self.user.GetWindowTextW(hwnd, caption, len(caption))
        if filename.casefold() not in caption.value.casefold():
            return False
        image = self.process_image(self.window_pid(hwnd))
        return image is not None and image.name.casefold() == "notepad.exe"

    def window_pid(self, hwnd):
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        return pid.value

    def process_image(self, pid):
        proc = self.kernel.OpenProcess(0x1000, False, pid)
        if not proc:
            return None
        try:
            image = c.create_unicode_buffer(1024)
            size = w.DWORD(len(image))
            if self.kernel.QueryFullProcessImageNameW(proc, 0, image, c.byref(size)):
                return Path(image.value)
            return None
        finally:
            self.kernel.CloseHandle(proc)

    def find_windows(self, accepts):
        matches = []

        @c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def examine(hwnd, _data):
            if accepts(hwnd):
                matches.append(hwnd)
            return True

        self.user.EnumWindows(examine, 0)
        return matches

    def windows(self, filename):
        return self.find_windows(lambda hwnd: self.notepad_window(hwnd, filename))

    def launch_app(self, executable):
        self.require_desktop()
        return subprocess.Popen([str(executable)], cwd=executable.parent)

    def inspect_app(self, process, executable):
        exit_code = process.poll()
        if exit_code is not None:
            return {"status": "exited", "exit_code": exit_code}
        if self.process_image(process.pid) != executable or process.poll() is not None:
            return {"status": "unconfirmed"}
        return {"status": "running", "pid": process.pid}

    def close_app(self, process, executable):
        self.require_desktop()
        state = self.inspect_app(process, executable)
        if state["status"] != "running":
            return state
        pid = process.pid
        windows = self.find_windows(lambda hwnd: self.user.IsWindowVisible(hwnd) and self.window_pid(hwnd) == pid)
        if len(windows) != 1 or self.user.GetWindow(windows[0], 4):  # GW_OWNER
            return {"status": "kept_open", "pid": pid, "reason": "no_unique_main_window"}
        target = windows[0]
        if (self.inspect_app(process, executable)["status"] != "running" or self.window_pid(target) != pid
                or not self.user.IsWindowVisible(target) or self.user.GetWindow(target, 4)):
            return {"status": "unconfirmed"}
        if not self.user.PostMessageW(target, 0x0010, 0, 0):  # WM_CLOSE
            return {"status": "kept_open", "pid": pid, "reason": "close_request_failed"}
        try:
            return {"status": "closed", "pid": pid, "exit_code": process.wait(timeout=2)}
        except subprocess.TimeoutExpired:
            return {"status": "kept_open", "pid": pid, "reason": "app_still_running"}

    def launch(self, path):
        self.require_desktop()
        windows = self.windows(path.name)
        if not windows:
            subprocess.Popen([str(self.notepad), str(path)], cwd=path.parent)
            for _ in range(30):
                time.sleep(.2)
                windows = self.windows(path.name)
                if windows:
                    break
        if len(windows) != 1:
            raise ValueError("未确认唯一的记事本测试窗口")
        return windows[0]

    def focus(self, hwnd, filename):
        self.require_desktop()
        if not self.notepad_window(hwnd, filename):
            raise ValueError("目标窗口已变更")
        self.user.ShowWindow(hwnd, 9)
        self.user.SetForegroundWindow(hwnd)
        if self.user.GetForegroundWindow() != hwnd:
            raise ValueError("Windows 未将目标窗口移到前台")

    def screenshot(self, hwnd, filename):
        self.focus(hwnd, filename)
        rect = w.RECT()
        if not self.user.GetWindowRect(hwnd, c.byref(rect)):
            raise ValueError("无法读取窗口范围")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if not 0 < width <= 1920 or not 0 < height <= 1080:
            raise ValueError("测试窗口超出截图范围")
        script = (
            "Add-Type -AssemblyName System.Drawing\n"
            f"$b=New-Object Drawing.Bitmap {width},{height}\n"
            "$g=[Drawing.Graphics]::FromImage($b)\n"
            f"$g.CopyFromScreen({rect.left},{rect.top},0,0,$b.Size)\n"
            "$m=New-Object IO.MemoryStream\n"
            "$b.Save($m,[Drawing.Imaging.ImageFormat]::Png)\n"
            "[Convert]::ToBase64String($m.ToArray())\n"
            "$m.Dispose(); $g.Dispose(); $b.Dispose()\n"
        )
        image = self._powershell(script)
        if self.user.GetForegroundWindow() != hwnd or not self.notepad_window(hwnd, filename):
            raise ValueError("截图期间目标窗口发生变化")
        return image

    def type_and_save(self, hwnd, filename, text):
        self.focus(hwnd, filename)
        # JSON text goes through stdin and is never interpolated as PowerShell code.
        script = (
            "param([string]$textPath,[long]$expected)\n"
            "Add-Type -AssemblyName System.Windows.Forms\n"
            "Add-Type -Name Gate -Namespace FishHome -MemberDefinition "
            "'[System.Runtime.InteropServices.DllImport(\"user32.dll\")] public static extern System.IntPtr GetForegroundWindow();'\n"
            "function Check { if ([FishHome.Gate]::GetForegroundWindow().ToInt64() -ne $expected) { throw 'Focus changed' } }\n"
            "$text=Get-Content -LiteralPath $textPath -Raw -Encoding UTF8 | ConvertFrom-Json\n"
            "Check; [Windows.Forms.Clipboard]::SetText($text)\n"
            "try { Check; [Windows.Forms.SendKeys]::SendWait('^a'); Check; [Windows.Forms.SendKeys]::SendWait('^v'); "
            "Check; [Windows.Forms.SendKeys]::SendWait('^s') } finally { [Windows.Forms.Clipboard]::Clear() }\n"
        )
        import tempfile

        with tempfile.TemporaryDirectory(prefix="fishhome-note-") as directory:
            scriptfile = Path(directory) / "write.ps1"
            textfile = Path(directory) / "input.json"
            scriptfile.write_text(script, encoding="utf-8")
            textfile.write_text(json.dumps(text), encoding="utf-8")
            result = subprocess.run([str(self.powershell), "-NoProfile", "-STA", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(scriptfile), str(textfile), str(hwnd)],
                capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ValueError("窗口未确认输入及保存")

    def _powershell(self, script):
        result = subprocess.run([str(self.powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not result.stdout.strip():
            raise ValueError("未取得测试窗口截图")
        return result.stdout.strip()

    def close(self, hwnd, filename):
        self.focus(hwnd, filename)
        self.user.PostMessageW(hwnd, 0x0010, 0, 0)
        for _ in range(12):
            time.sleep(.15)
            if not self.user.IsWindow(hwnd):
                return True
        return False

    def memory(self):
        value = Memory(length=c.sizeof(Memory))
        if not self.kernel.GlobalMemoryStatusEx(c.byref(value)):
            raise ValueError("未取得内存状态")
        return {"total_bytes": value.total, "available_bytes": value.available}


class DesktopBroker:
    def __init__(self, workspace, driver, *, apps=None, apps_root=None, packages_root=None):
        self.workspace, self.driver = workspace.resolve(), driver
        self.active = {}
        self.apps = apps or {}
        self.apps_root = apps_root
        self.packages_root = packages_root
        self.active_apps = {}

    def list_apps(self):
        rows = []
        for ident in sorted(self.apps):
            active = self.active_apps.get(ident)
            state = self.driver.inspect_app(*active) if active else {"status": "not_started"}
            executable, archive = personal_app_spec(ident, self.apps[ident])
            if archive:
                try:
                    personal_executable(self.apps_root, executable)
                    package_status = "installed"
                except FileNotFoundError:
                    package_status = "not_installed"
                except (OSError, ValueError):
                    package_status = "unconfirmed"
                state = {**state, "package_status": package_status}
            rows.append({"app_id": ident, **state})
        return rows

    def operate_app(self, op, ident):
        if not isinstance(ident, str) or ident not in self.apps or self.apps_root is None:
            raise ValueError("没有登记这个个人软件")
        executable, archive = personal_app_spec(ident, self.apps[ident])
        active = self.active_apps.get(ident)
        if op == "app_install":
            if not archive or self.packages_root is None:
                raise ValueError("该软件没有登记本地安装包")
            if active and active[0].poll() is None:
                return {"app_id": ident, "status": "kept_open"}
            status = install_personal_app(self.apps_root, self.packages_root, ident, executable, archive)
            return {"app_id": ident, "status": status}
        if op == "app_launch":
            if active is None or active[0].poll() is not None:
                executable = personal_executable(self.apps_root, executable)
                active = (self.driver.launch_app(executable), executable)
                self.active_apps[ident] = active
            state = self.driver.inspect_app(*active)
        elif active:
            state = self.driver.close_app(*active)
        else:
            state = {"status": "not_started"}
        return {"app_id": ident, **state}

    def execute(self, op, name="", text=""):
        if op == "memory":
            return self.driver.memory()
        if op == "list":
            return {"registered": ["notepad", *sorted(self.apps)], "notes": sorted(self.active),
                    "personal_apps": self.list_apps()}
        if op == "app_list":
            return {"personal_apps": self.list_apps()}
        if op in {"app_install", "app_launch", "app_close"}:
            if text:
                raise ValueError("个人软件仅接受登记 ID")
            return self.operate_app(op, name)
        if not isinstance(name, str) or not NOTE.fullmatch(name):
            raise ValueError("请用工作区内的英文或数字 .txt 文件名")
        path = self.workspace / name
        if path.resolve() != path:
            raise ValueError("测试文件必须直接位于工作区")
        if op == "open":
            path.touch(exist_ok=True)
            self.active[name] = self.driver.launch(path)
            return {"status": "opened", "file": name}
        hwnd = self.active.get(name)
        if hwnd is None:
            raise ValueError("请先打开这个记事本文件")
        if op == "write":
            if not isinstance(text, str) or not 1 <= len(text) <= 2000 or "\0" in text:
                raise ValueError("测试文本须在 1 至 2000 字之间")
            self.driver.type_and_save(hwnd, name, text)
            readback = path.read_text(encoding="utf-8-sig")
            if readback.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
                raise ValueError("记事本保存回读与输入不符")
            return {"status": "saved", "file": name, "characters": len(readback)}
        if op == "capture":
            return {"status": "captured", "png": self.driver.screenshot(hwnd, name)}
        if op == "close":
            closed = self.driver.close(hwnd, name)
            if closed:
                self.active.pop(name)
            return {"status": "closed" if closed else "kept_open", "file": name}
        raise ValueError("未登记的桌面操作")


def serve(broker, token):
    expected = ("Bearer " + token).encode()

    class Request(BaseHTTPRequestHandler):
        def do_POST(self):
            if (self.path != "/desktop" or self.headers.get("Host") != f"127.0.0.1:{PORT}"
                    or not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected)):
                self.send_error(401)
                return
            try:
                count = int(self.headers.get("Content-Length", "0"))
                if not 1 <= count <= 24_000:
                    raise ValueError("桌面请求超出大小限制")
                body = json.loads(self.rfile.read(count))
                closing = body == {"op": "shutdown"}
                if closing:
                    if (any(process.poll() is None for process, _ in broker.active_apps.values())
                            or any(broker.driver.windows(name) for name in broker.active)):
                        raise ValueError("还有个人程序或记事本窗口，请正常关闭后再停桌面服务")
                    result = {"status": "stopping"}
                else:
                    result = broker.execute(**body)
                code = 200
            except (ValueError, TypeError, OSError, subprocess.SubprocessError) as exc:
                result, code = {"error": str(exc) if isinstance(exc, ValueError) else "桌面操作未完成"}, 409
            payload = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            if code == 200 and closing:
                Thread(target=http.shutdown, daemon=True).start()

        def log_message(self, *args):
            return

    with HTTPServer(("127.0.0.1", PORT), Request) as http:
        http.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--init-token", action="store_true")
    args = parser.parse_args()
    if os.environ.get("USERNAME", "").casefold() != ACCOUNT.casefold():
        parser.error("需要登录 FishHomeDesktop 标准账户")
    workspace = args.workspace.resolve()
    if not workspace.is_relative_to(Path(os.environ["USERPROFILE"]).resolve()):
        parser.error("工作区须在专用账户的目录中")
    if not args.token_file.resolve().is_relative_to(Path(os.environ["USERPROFILE"]).resolve()):
        parser.error("凭据文件须在专用账户的目录中")
    if args.init_token:
        args.token_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.token_file.open("x", encoding="ascii") as target:
                target.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    token = args.token_file.read_text(encoding="utf-8-sig").strip()
    if len(token) < 32:
        parser.error("缺少本机桌面接口凭据")
    workspace.mkdir(parents=True, exist_ok=True)
    broker_root = Path(__file__).resolve().parent
    try:
        apps = load_personal_apps(broker_root / APP_MANIFEST)
    except (OSError, ValueError) as exc:
        print(f"无法加载个人软件配置：{exc}", file=sys.stderr, flush=True)
        apps = {}
    apps_root = Path(os.environ["LOCALAPPDATA"]) / "FishHome/desktop/apps"
    serve(DesktopBroker(workspace, WindowsNote(), apps=apps, apps_root=apps_root,
                       packages_root=broker_root / "packages"), token)


if __name__ == "__main__":
    main()
