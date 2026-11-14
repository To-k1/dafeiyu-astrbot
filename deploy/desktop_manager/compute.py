"""Provision the files shared by the two DSH launchers and the Compute worker."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import winreg

import win32security

from deploy.dsh_bridge.render_compute_preset import render


ACCOUNT = "FishHomeCompute"
PORT = 59936


def sid(name):
    identity, _, _ = win32security.LookupAccountName(None, name)
    return win32security.ConvertSidToStringSid(identity)


def profile(account):
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList" + "\\" + sid(account)) as key:
        return Path(os.path.expandvars(winreg.QueryValueEx(key, "ProfileImagePath")[0]))


def acl(path, *args):
    executable = Path(os.environ["SystemRoot"]) / "System32/icacls.exe"
    subprocess.run([str(executable), str(path), *args], check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def prepare(directory, source, archive, python, token, runner):
    directory.mkdir(exist_ok=True)
    if directory.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("计算组件目录不能是链接")
    owner = win32security.GetFileSecurity(str(directory), win32security.OWNER_SECURITY_INFORMATION)
    current_sid = sid(os.environ["USERNAME"])
    if win32security.ConvertSidToStringSid(owner.GetSecurityDescriptorOwner()) != current_sid:
        raise ValueError("计算组件目录不属于当前运维账户")
    acl(directory, "/inheritance:r", "/grant:r", f"*{current_sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F", f"*{sid(ACCOUNT)}:(OI)(CI)RX", f"*{sid(runner)}:(OI)(CI)RX")
    for filename in ("compute_mcp.py", "probe_appcontainer.py", "compute_proxy.py"):
        shutil.copyfile(source / filename, directory / filename)
    cached = directory / "python.tar.gz"
    if not cached.is_file():
        partial = cached.with_suffix(".tmp")
        shutil.copyfile(archive, partial)
        partial.replace(cached)
    runtime = directory / "python"
    if not (runtime / "python.exe").is_file():
        # Extract in a sibling directory so an interrupted unpack never becomes active.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(dir=directory) as stage:
            shutil.unpack_archive(cached, stage, filter="data")
            (Path(stage) / "python").replace(runtime)
    acl(runtime, "/grant:r", f"*{sid(ACCOUNT)}:(OI)(CI)RX", f"*{sid(runner)}:(OI)(CI)RX")
    ticket = directory / "worker.token"
    ticket.touch(exist_ok=True)
    acl(ticket, "/inheritance:r", "/grant:r", f"*{current_sid}:F", "*S-1-5-18:F",
        "*S-1-5-32-544:F", f"*{sid(runner)}:R")
    ticket.write_text(token, encoding="ascii")
    overlay = directory / "compute.yml"
    overlay.write_text(render(runtime / "python.exe", directory / "compute_proxy.py", PORT, ticket), encoding="utf-8")
    return runtime / "python.exe"
