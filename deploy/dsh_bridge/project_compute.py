"""Project file checkout and wheel preparation for the restricted compute worker."""

import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4


PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}(?:==[A-Za-z0-9][A-Za-z0-9._+!-]{0,63})?")
WHEELS = ".fishhome-wheels"
DEPS = ".fishhome-deps"
PIP_ENTRY = ("import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
             "sys.argv[0]='pip'; runpy.run_module('pip',run_name='__main__')")
PIP_OFFLINE_ENTRY = """\
import os
from pathlib import Path
import tempfile

scratch = Path.cwd() / ".fishhome-pip-tmp"
scratch.mkdir(exist_ok=True)
tempfile.tempdir = str(scratch)
mkdir = os.mkdir

def scratch_mkdir(path, mode=0o777, *, dir_fd=None):
    # Python's Windows 0700 ACL omits the AppContainer SID. Use the inherited
    # ACL from this disposable workspace only for pip's scratch directories.
    if dir_fd is None and mode == 0o700 and Path(path).parent == scratch:
        mkdir(path)
    else:
        mkdir(path, mode, dir_fd=dir_fd)

os.mkdir = scratch_mkdir
""" + PIP_ENTRY


def plain(path, *, directory=False):
    info = path.lstat()
    mode = stat.S_ISDIR if directory else stat.S_ISREG
    if (not mode(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not directory and info.st_nlink != 1)):
        raise ValueError("项目只接受普通文件和目录")


def project_directory(root, name, *, create=False):
    if not isinstance(name, str) or not PROJECT.fullmatch(name) or PureWindowsPath(name).is_reserved():
        raise ValueError("请使用由字母、数字、下划线或短横线组成的项目名")
    plain(root, directory=True)
    path = root / name
    if create:
        path.mkdir(exist_ok=True)
    plain(path, directory=True)
    return path


def relative_file(path):
    if not isinstance(path, str) or not path or any(char in path for char in "\\:\x00\r\n"):
        raise ValueError("请使用项目内的相对文件名")
    parts = path.split("/")
    for part in parts:
        if (part.casefold() in {".", "..", ".git", WHEELS, DEPS} or not part or part.endswith((" ", "."))
                or any(char in part for char in '<>"|?*') or PureWindowsPath(part).is_reserved()):
            raise ValueError("项目文件路径超出允许范围")
    return Path(*parts)


def checkout(project, stage):
    for item in project.iterdir():
        if item.name.casefold() in {".git", DEPS}:
            continue
        target = stage / item.name
        if item.is_dir():
            plain(item, directory=True)
            target.mkdir()
            checkout(item, target)
        else:
            plain(item)
            shutil.copyfile(item, target)


def _resolve_parents(root, relative, *, create=False):
    parent = root
    for part in relative.parts[:-1]:
        parent /= part
        if create:
            parent.mkdir(exist_ok=True)
        plain(parent, directory=True)
    return parent / relative.name


def _snapshot(project, path):
    try:
        target = _resolve_parents(project, path)
        plain(target)
        info = target.stat()
        return info.st_mtime_ns, info.st_size
    except FileNotFoundError:
        return None


def output_baselines(project, names):
    if not isinstance(names, list):
        raise ValueError("outputs 必须是项目文件名列表")
    return {relative_file(name): _snapshot(project, relative_file(name)) for name in names}


def remove_file(project, name):
    relative = relative_file(name)
    target = _resolve_parents(project, relative)
    plain(target)
    target.unlink()
    return {"status": "removed", "file": relative.as_posix()}


def publish_outputs(stage, project, baseline):
    copies = []
    for path, original in baseline.items():
        src = _resolve_parents(stage, path)
        plain(src)
        if _snapshot(project, path) != original:
            raise ValueError("项目输出在运行期间已被其他进程修改")
        dst = _resolve_parents(project, path, create=True)
        copies.append((path, src, dst, original))
    for path, src, dst, original in copies:
        with NamedTemporaryFile(dir=dst.parent, delete=False) as output:
            temp = Path(output.name)
            try:
                with src.open("rb") as contents:
                    shutil.copyfileobj(contents, output)
                output.close()
                if _snapshot(project, path) != original:
                    raise ValueError("项目输出在运行期间已被其他进程修改")
                os.replace(temp, dst)
            finally:
                temp.unlink(missing_ok=True)
    return [path.as_posix() for path in baseline]


def pip_args(runtime, *, offline=False):
    wheel, = (runtime / "Lib/ensurepip/_bundled").glob("pip-*.whl")
    entry = PIP_OFFLINE_ENTRY if offline else PIP_ENTRY
    return ["-I", "-B", "-c", entry, str(wheel), "--isolated", "--disable-pip-version-check"]


def prepare_wheels(project, packages):
    if not isinstance(packages, list) or not packages or any(not isinstance(p, str) or not PACKAGE.fullmatch(p) for p in packages):
        raise ValueError("包名仅接受 PyPI 名称或 name==version")
    wheelhouse = project / WHEELS
    if os.path.lexists(wheelhouse):
        plain(wheelhouse, directory=True)
    runtime = Path(sys.executable).parent
    with TemporaryDirectory(prefix="fishhome-wheel-download-", dir=Path.home()) as directory:
        stage = Path(directory)
        env = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
        env["PIP_CONFIG_FILE"] = os.devnull
        try:
            install = subprocess.run([sys.executable, *pip_args(runtime), "download", "--only-binary=:all:",
                "--index-url", "https://pypi.org/simple", "--no-input", "--no-cache-dir", "--timeout", "8",
                "--retries", "1", "--dest", str(stage), *packages], env=env, cwd=stage,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"status": "failed", "detail": "等待 PyPI 项目依赖超时。"}
        if install.returncode != 0:
            return {"status": "failed", "detail": "PyPI 未提供此次请求需要的兼容预编译包，或下载失败。"}
        wheels = sorted(stage.glob("*.whl"))
        if not wheels:
            return {"status": "failed", "detail": "未取得项目依赖包。"}
        for wheel in wheels:
            plain(wheel)
        # Keep the previous dependency set when a download fails.
        # A 0700 temporary directory on Windows would give the promoted wheelhouse
        # a private ACL, hiding it from the other account that owns the project.
        swap = project / (".wheel-set-" + uuid4().hex)
        swap.mkdir()
        try:
            saved = swap / "previous"
            if wheelhouse.is_dir():
                wheelhouse.replace(saved)
            try:
                stage_target = swap / "ready"
                stage_target.mkdir()
                for wheel in wheels:
                    shutil.copyfile(wheel, stage_target / wheel.name)
                stage_target.replace(wheelhouse)
            except BaseException:
                if saved.is_dir():
                    saved.replace(wheelhouse)
                raise
        finally:
            if swap.resolve(strict=True).parent != project.resolve(strict=True):
                raise ValueError("依赖暂存目录超出项目")
            shutil.rmtree(swap)
        return {"status": "prepared", "packages": packages, "wheels": [wheel.name for wheel in wheels]}
