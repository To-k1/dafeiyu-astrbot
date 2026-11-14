"""Exercise portable Python in a disposable, network-free Windows AppContainer."""

import ctypes as ct
from contextlib import contextmanager
from ctypes import wintypes as win
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile


class SecurityCapabilities(ct.Structure):
    _fields_ = [("sid", ct.c_void_p), ("capabilities", ct.c_void_p),
                ("count", win.DWORD), ("reserved", win.DWORD)]


class StartupInfo(ct.Structure):
    _fields_ = [("cb", win.DWORD), ("reserved", win.LPWSTR), ("desktop", win.LPWSTR),
                ("title", win.LPWSTR), ("x", win.DWORD), ("y", win.DWORD),
                ("x_size", win.DWORD), ("y_size", win.DWORD), ("x_count", win.DWORD),
                ("y_count", win.DWORD), ("fill", win.DWORD), ("flags", win.DWORD),
                ("show", win.WORD), ("reserved_size", win.WORD), ("reserved2", ct.c_void_p),
                ("stdin", win.HANDLE), ("stdout", win.HANDLE), ("stderr", win.HANDLE),
                ("attributes", ct.c_void_p)]


class ProcessInfo(ct.Structure):
    _fields_ = [("process", win.HANDLE), ("thread", win.HANDLE),
                ("pid", win.DWORD), ("tid", win.DWORD)]


class BasicJobLimits(ct.Structure):
    _fields_ = [("process_time", ct.c_int64), ("job_time", ct.c_int64),
                ("flags", win.DWORD), ("min_working_set", ct.c_size_t),
                ("max_working_set", ct.c_size_t), ("active_processes", win.DWORD),
                ("affinity", ct.c_size_t), ("priority", win.DWORD), ("scheduling", win.DWORD)]


class JobLimits(ct.Structure):
    _fields_ = [("basic", BasicJobLimits), ("io", ct.c_uint64 * 6),
                ("process_memory", ct.c_size_t), ("job_memory", ct.c_size_t),
                ("peak_process_memory", ct.c_size_t), ("peak_job_memory", ct.c_size_t)]


def bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype = result
    fn.argtypes = args
    return fn


def checked(result):
    if not result:
        raise ct.WinError(ct.get_last_error())


def launch(kernel, sid, executable, arguments, work, timeout):
    ptr, size = ct.c_void_p, ct.c_size_t
    initialize = bind(kernel, "InitializeProcThreadAttributeList", win.BOOL,
                      ptr, win.DWORD, win.DWORD, ct.POINTER(size))
    update = bind(kernel, "UpdateProcThreadAttribute", win.BOOL,
                  ptr, win.DWORD, size, ptr, size, ptr, ptr)
    delete = bind(kernel, "DeleteProcThreadAttributeList", None, ptr)
    create = bind(kernel, "CreateProcessW", win.BOOL, win.LPCWSTR, win.LPWSTR,
                  ptr, ptr, win.BOOL, win.DWORD, ptr, win.LPCWSTR,
                  ct.POINTER(StartupInfo), ct.POINTER(ProcessInfo))
    wait = bind(kernel, "WaitForSingleObject", win.DWORD, win.HANDLE, win.DWORD)
    status = bind(kernel, "GetExitCodeProcess", win.BOOL, win.HANDLE, ct.POINTER(win.DWORD))
    close = bind(kernel, "CloseHandle", win.BOOL, win.HANDLE)
    create_job = bind(kernel, "CreateJobObjectW", win.HANDLE, ptr, win.LPCWSTR)
    configure_job = bind(kernel, "SetInformationJobObject", win.BOOL, win.HANDLE,
                         ct.c_int, ptr, win.DWORD)

    length = size()
    initialize(None, 2, 0, ct.byref(length))
    attributes = ct.create_string_buffer(length.value)
    checked(initialize(attributes, 2, 0, ct.byref(length)))
    job = None
    try:
        job = create_job(None, None)
        checked(job)
        limits = JobLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        checked(configure_job(job, 9, ct.byref(limits), ct.sizeof(limits)))
        job_handle = win.HANDLE(job)
        checked(update(attributes, 0, 0x0002000D, ct.byref(job_handle), ct.sizeof(job_handle), None, None))
        capabilities = SecurityCapabilities(sid, None, 0, 0)
        security_capabilities_attribute = 0x00020009
        checked(update(attributes, 0, security_capabilities_attribute, ct.byref(capabilities),
                       ct.sizeof(capabilities), None, None))
        startup = StartupInfo(cb=ct.sizeof(StartupInfo), attributes=ct.addressof(attributes))
        process = ProcessInfo()
        command = ct.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
        system_root = os.environ["SystemRoot"]
        variables = {key: os.environ[key] for key in ("SystemRoot", "SystemDrive", "LOCALAPPDATA", "USERPROFILE")}
        variables.update({"PATH": str(Path(system_root) / "System32"),
                          "TEMP": str(work), "TMP": str(work), "PYTHONUTF8": "1"})
        environment = ct.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(variables.items())) + "\0")
        extended_startup, unicode_environment, no_window = 0x80000, 0x400, 0x8000000
        checked(create(str(executable), command, None, None, False,
                       extended_startup | unicode_environment | no_window, environment,
                       str(work), ct.byref(startup), ct.byref(process)))
        try:
            outcome = wait(process.process, timeout)
            if outcome == 258:
                close(job)
                job = None
                wait(process.process, 5_000)
                return "timeout"
            checked(outcome == 0)
            code = win.DWORD()
            checked(status(process.process, ct.byref(code)))
            return f"0x{code.value:08x}"
        finally:
            close(process.thread)
            close(process.process)
    finally:
        delete(attributes)
        if job:
            close(job)


def grant(path, *arguments):
    subprocess.run(["icacls.exe", str(path), *arguments], check=True, capture_output=True)


@contextmanager
def appcontainer():
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    profiles = ct.WinDLL("userenv", use_last_error=True)
    security = ct.WinDLL("advapi32", use_last_error=True)
    ptr = ct.c_void_p
    create_profile = bind(profiles, "CreateAppContainerProfile", win.LONG,
                          win.LPCWSTR, win.LPCWSTR, win.LPCWSTR, ptr, win.DWORD, ct.POINTER(ptr))
    delete_profile = bind(profiles, "DeleteAppContainerProfile", win.LONG, win.LPCWSTR)
    convert_sid = bind(security, "ConvertSidToStringSidW", win.BOOL, ptr, ct.POINTER(win.LPWSTR))
    free_sid = bind(security, "FreeSid", ptr, ptr)
    free_string = bind(kernel, "LocalFree", ptr, ptr)

    name = "FishHomeProbe" + uuid4().hex
    sid = ptr()
    result = create_profile(name, name, "Disposable FishHome Python probe", None, 0, ct.byref(sid))
    if result < 0:
        raise OSError(f"CreateAppContainerProfile failed: 0x{result & 0xffffffff:08x}")
    try:
        string_sid = win.LPWSTR()
        checked(convert_sid(sid, ct.byref(string_sid)))
        try:
            ace = "*" + string_sid.value
        finally:
            free_string(ct.cast(string_sid, ptr))
        yield kernel, sid, ace
    finally:
        free_sid(sid)
        result = delete_profile(name)
        if result < 0:
            raise OSError(f"DeleteAppContainerProfile failed: 0x{result & 0xffffffff:08x}")


def probe(install):
    with appcontainer() as (kernel, sid, ace):
        with TemporaryDirectory(prefix="FishHomeAppContainer-", dir=Path.home()) as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            outside = root / "outside.txt"
            outside.write_text("disposable fixture", encoding="ascii")
            shutil.unpack_archive(install / "downloads/python-3.12.10-windows-x64.tar.gz", root, filter="data")
            runtime = root / "python"
            verifier = runtime / "verify_python_workspace.py"
            shutil.copyfile(install / "repo/deploy/dsh_bridge/verify_python_workspace.py", verifier)
            child = runtime / "probe_child.py"
            child.write_text(CHILD, encoding="utf-8")
            wheel = work / "fishhome_probe-0.0.1-py3-none-any.whl"
            with ZipFile(wheel, "w") as package:
                package.writestr("fishhome_probe.py", "VALUE = 17\n")
                metadata = "fishhome_probe-0.0.1.dist-info/"
                package.writestr(metadata + "METADATA", "Metadata-Version: 2.1\nName: fishhome-probe\nVersion: 0.0.1\n")
                package.writestr(metadata + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                package.writestr(metadata + "RECORD", "")
            grant(root, "/grant", f"{ace}:RX")
            grant(runtime, "/grant", f"{ace}:(OI)(CI)RX")
            grant(work, "/grant", f"{ace}:(OI)(CI)M")
            grant(work, "/setintegritylevel", "(OI)(CI)L")
            no_error_dialogs = 0x0001 | 0x0002 | 0x8000
            kernel.SetErrorMode(no_error_dialogs)
            executable = runtime / "python.exe"
            startup = launch(kernel, sid, executable, ["-I", "-S", "-c", "open('startup.txt','w').close()"], work, 12_000)
            if startup != "0x00000000" or not (work / "startup.txt").is_file():
                return {"startup": startup, "wrote_startup": (work / "startup.txt").is_file()}
            code = launch(kernel, sid, executable, ["-B", str(child)], work, 60_000)
            receipt = work / "receipt.json"
            evidence = {"exit": code, "child": json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else None}
            environment = work / "venv"
            evidence["venv_without_pip"] = launch(kernel, sid, executable,
                ["-m", "venv", "--without-pip", str(environment)], work, 12_000)
            interpreter = environment / "Scripts/python.exe"
            if interpreter.is_file():
                evidence["venv_python"] = launch(kernel, sid, interpreter,
                    ["-c", "open('venv-startup.txt','w').close()"], work, 12_000)
                pip_wheel, = (runtime / "Lib/ensurepip/_bundled").glob("pip-*.whl")
                bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
                             "sys.argv[0]='pip'; runpy.run_module('pip',run_name='__main__')")
                evidence["offline_pip"] = launch(kernel, sid, interpreter,
                    ["-c", bootstrap, str(pip_wheel), "--isolated", "install", "--no-index", "--no-deps",
                     "--disable-pip-version-check", "--no-compile", "--no-cache-dir", "--progress-bar", "off",
                     "--log", str(work / "pip.log"), str(wheel)], work, 45_000)
                if evidence["offline_pip"] != "0x00000000" and (work / "pip.log").is_file():
                    evidence["pip_log_tail"] = (work / "pip.log").read_text(encoding="utf-8", errors="replace")[-950:]
                evidence["installed_import"] = launch(kernel, sid, interpreter,
                    ["-c", "from fishhome_probe import VALUE; assert VALUE == 17"], work, 12_000)
            nested = ("import ctypes as c; from ctypes import wintypes as w; "
                      "a=c.windll.advapi32; t=w.HANDLE(); k=w.DWORD(); n=w.DWORD(); "
                      "assert a.OpenProcessToken(w.HANDLE(-1),8,c.byref(t)); "
                      "assert a.GetTokenInformation(t,29,c.byref(k),4,c.byref(n)) and k.value")
            spawn_code = ("import subprocess,sys; "
                          f"subprocess.run([sys.executable,'-c',{nested!r}], check=True, "
                          "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)")
            evidence["python_child_process"] = launch(kernel, sid, executable,
                ["-c", spawn_code], work, 12_000)
            return evidence


CHILD = r'''
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
work = Path.cwd()
receipt = work / "receipt.json"
def save(result):
    receipt.write_text(json.dumps(result), encoding="utf-8")

result = {"started": True}
save(result)
import socket
from verify_python_workspace import protected_access
token = wintypes.HANDLE()
access = ctypes.WinDLL("advapi32", use_last_error=True)
if not access.OpenProcessToken(wintypes.HANDLE(-1), 8, ctypes.byref(token)):
    raise ctypes.WinError(ctypes.get_last_error())
try:
    in_container, size = wintypes.DWORD(), wintypes.DWORD()
    if not access.GetTokenInformation(token, 29, ctypes.byref(in_container), 4, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    result["appcontainer_token"] = bool(in_container.value)
finally:
    ctypes.windll.kernel32.CloseHandle(token)
result["credential_variables_absent"] = not any(
    key in os.environ for key in ("DEEPSEEK_API_KEY", "FISHHOME_DSH_GATEWAY_TOKEN", "FISH_MANAGER_TOKEN")
)
for port in (5037, 5555, 59934, 59935, 6197, 8009, 8011):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            result[str(port)] = "connected"
    except OSError as exc:
        result[str(port)] = getattr(exc, "winerror", None) or type(exc).__name__
    save(result)
outside = work.parent / "outside.txt"
result["outside"] = [protected_access(outside, mode) for mode in ("rb", "r+b")]
save(result)
'''


if __name__ == "__main__":
    try:
        evidence = probe(Path(sys.argv[1]))
    except Exception as exc:
        evidence = {"error": f"{type(exc).__name__}: {exc}"}
    Path(sys.argv[2]).write_text(json.dumps(evidence), encoding="utf-8")
