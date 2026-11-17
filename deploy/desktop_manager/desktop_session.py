"""Launch a separate account in the manager's current interactive desktop.

The account ACEs live until the interactive session ends.  They intentionally
apply to this account (CreateProcessWithLogonW creates a new logon SID).
"""
import ctypes
from contextlib import contextmanager
from ctypes import wintypes

import win32con
import win32security
import win32service

from .windows import run_as

# Required object-specific access, plus READ_CONTROL; no WRITE_DAC grant.
_STATION_ACCESS = 0x00020337
_DESKTOP_ACCESS = 0x000200FF
_SECURITY_ACCESS = win32con.READ_CONTROL | win32con.WRITE_DAC


def session_id(pid):
    """Return the Windows session containing the given process."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value


def get_session():
    """Return non-secret evidence that this is an unlocked interactive session."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcessId.restype = wintypes.DWORD
    current_session = session_id(kernel.GetCurrentProcessId())
    station = win32service.GetUserObjectInformation(
        win32service.GetProcessWindowStation(), win32service.UOI_NAME)
    if current_session == 0 or station.casefold() != "winsta0":
        raise RuntimeError("需要已登录的交互式 Windows 会话")
    try:
        desktop = win32service.OpenInputDesktop(0, False, 0x0001)
        try:
            name = win32service.GetUserObjectInformation(desktop, win32service.UOI_NAME)
        finally:
            desktop.CloseDesktop()
    except Exception as exc:
        raise RuntimeError("无法访问当前输入桌面") from exc
    if name.casefold() != "default":
        raise RuntimeError("当前桌面已锁定或不可用")
    return {"session_id": current_session, "window_station": station, "desktop": name}


def _grant(handle, sid, mask):
    info = win32security.GetSecurityInfo(handle, win32security.SE_WINDOW_OBJECT,
                                         win32security.DACL_SECURITY_INFORMATION)
    acl = info.GetSecurityDescriptorDacl()
    # A null DACL already grants access to everyone; do not change its meaning.
    if acl is None:
        return
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if (ace[0][0] == win32con.ACCESS_ALLOWED_ACE_TYPE and ace[0][1] == 0
                and ace[2] == sid and ace[1] & mask == mask):
            return
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, 0, mask, sid)
    win32security.SetSecurityInfo(handle, win32security.SE_WINDOW_OBJECT,
                                 win32security.DACL_SECURITY_INFORMATION,
                                 None, None, acl, None)


@contextmanager
def desktop_access(account):
    """Grant account access while retaining all existing session object ACEs."""
    get_session()
    sid, _, _ = win32security.LookupAccountName(None, account)
    station = win32service.OpenWindowStation("winsta0", False, _SECURITY_ACCESS)
    try:
        desktop = win32service.OpenDesktop("default", 0, False, _SECURITY_ACCESS)
        try:
            _grant(station, sid, _STATION_ACCESS)
            _grant(desktop, sid, _DESKTOP_ACCESS)
            yield
        finally:
            desktop.CloseDesktop()
    finally:
        station.CloseWindowStation()


def launch_desktop(account, password, argv, env):
    """Start the broker under account credentials on the current desktop."""
    with desktop_access(account):
        return run_as(account, password, argv, env=env)

