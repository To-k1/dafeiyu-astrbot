"""Windows process and credential helpers for the desktop manager."""

import ctypes as ct
import json
import os
import subprocess
from contextlib import contextmanager
from ctypes import wintypes as w

import win32con
import win32crypt
import win32security


class Startup(ct.Structure):
    _fields_ = [
        ("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        *[(name, w.DWORD) for name in ("x", "y", "sx", "sy", "cx", "cy", "fill", "flags")],
        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved2", ct.c_void_p),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE),
    ]


class ProcessInfo(ct.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


def atomic_write(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


class Credentials:
    def __init__(self, path):
        self.path = path
        self.values = (json.loads(win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)[1])
                       if path.is_file() else {})

    def save(self):
        protected = win32crypt.CryptProtectData(json.dumps(self.values).encode(), "FishHome", None, None, None, 1)
        atomic_write(self.path, protected)


@contextmanager
def impersonate(username, password):
    token = win32security.LogonUser(username, os.environ["COMPUTERNAME"], password,
                                    win32con.LOGON32_LOGON_INTERACTIVE, win32con.LOGON32_PROVIDER_DEFAULT)
    try:
        win32security.ImpersonateLoggedOnUser(token)
        try:
            yield token
        finally:
            win32security.RevertToSelf()
    finally:
        token.Close()


def run_as(username, password, argv, *, wait=False):
    adv = ct.WinDLL("advapi32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    create = adv.CreateProcessWithLogonW
    create.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.LPCWSTR, w.LPWSTR,
                       w.DWORD, ct.c_void_p, w.LPCWSTR, ct.POINTER(Startup), ct.POINTER(ProcessInfo)]
    create.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ct.POINTER(w.DWORD)]
    startup, process = Startup(size=ct.sizeof(Startup)), ProcessInfo()
    command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
    if not create(username, os.environ["COMPUTERNAME"], password, 1, argv[0], command,
                  subprocess.CREATE_NO_WINDOW, None, os.environ["SystemRoot"], ct.byref(startup), ct.byref(process)):
        raise ct.WinError(ct.get_last_error())
    try:
        if wait:
            code = w.DWORD()
            if kernel.WaitForSingleObject(process.process, 7000) != 0 or not kernel.GetExitCodeProcess(process.process, ct.byref(code)):
                raise RuntimeError("等待 Windows 进程退出超时")
            return code.value
        return process.pid
    finally:
        kernel.CloseHandle(process.thread)
        kernel.CloseHandle(process.process)
