"""List and stage selected members of an imported QQ ZIP in the shared workspace."""

import json
import os
import re
import shutil
import stat
import zipfile
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4


ARCHIVE_PATH = re.compile(r"shared/qq-inbox/([\w-][\w.-]{0,95}\.zip)", re.IGNORECASE)
MAX_BYTES = 128 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_NAME = 512


def file_identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def safe_component(name):
    return (bool(name) and name not in {".", ".."} and not name.endswith((".", " "))
            and not PureWindowsPath(name).is_reserved()
            and not any(ord(char) < 32 or char in '<>:"\\|?*' for char in name))


def regular_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def extractable(info, counts):
    return (not info.is_dir() and not info.flag_bits & 1
            and stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG}
            and not info.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT
            and counts[info.filename.casefold()] == 1
            and len(info.filename) <= MAX_NAME
            and all(safe_component(part) for part in info.filename.split("/"))
            and info.file_size <= MAX_BYTES)


def page_size(total, offset, limit, maximum):
    if type(offset) is not int or not 0 <= offset <= total or type(limit) is not int or not 0 <= limit <= maximum:
        raise ValueError("ZIP 分页位置或长度无效")
    return min(total, offset + (limit or maximum))


def list_page(items, counts, offset, limit):
    end = page_size(len(items), offset, limit, 100)
    return {"entries": [{"member": info.filename[:MAX_NAME], "bytes": info.file_size, "directory": info.is_dir(),
                         "extractable": extractable(info, counts)} for info in items[offset:end]],
            "total_entries": len(items), "offset": offset,
            "next_offset": end if end < len(items) else None}


def pick(archive, name, counts):
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise ValueError("ZIP 中没有该条目，请使用清单中的完整名称") from None
    if not extractable(info, counts):
        raise ValueError("该 ZIP 条目不可读取或解包")
    return info


def read_text(archive, info, offset, limit):
    if info.file_size > MAX_TEXT_BYTES:
        raise ValueError("该文本超过 16 MiB，请解包选中的文件后交给 DSH 分段处理")
    content = archive.read(info)
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("该条目不是 UTF-8 文本，可先解包再按原编码读取") from None
    if "\0" in decoded:
        raise ValueError("该条目不是可直接读取的文本")
    end = page_size(len(decoded), offset, limit, 6000)
    return {"member": info.filename, "text": decoded[offset:end], "bytes_total": len(content),
            "total_chars": len(decoded), "offset": offset,
            "next_offset": end if end < len(decoded) else None, "complete": end == len(decoded)}


def stage(root, archive, selected):
    if sum(info.file_size for info in selected) > MAX_BYTES:
        raise ValueError("单次选择性解包最多 128 MiB，请分批选择")
    parent = root / "qq-zip"
    expected = root.resolve(strict=True) / parent.name
    parent.mkdir(exist_ok=True)
    info = parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or parent.resolve(strict=True) != expected):
        raise PermissionError("ZIP 任务目录必须是普通目录")
    # mkdtemp uses 0700, which protects the directory's DACL on Windows.
    # Default mkdir permissions inherit the shared workspace's Runner access.
    directory = parent / ("task-" + uuid4().hex)
    directory.mkdir()
    relative = "shared/qq-zip/" + directory.name
    outputs = []
    created = []
    try:
        if directory.resolve(strict=True).parent != expected:
            raise PermissionError("ZIP 任务目录已改变")
        for index, info in enumerate(selected, 1):
            name = PurePosixPath(info.filename)
            stem = re.sub(r"[^\w.-]", "_", name.stem)[:60]
            suffix = re.sub(r"[^\w.]", "_", name.suffix)[:12]
            target = directory / f"{index:03d}-{stem}{suffix}"
            with archive.open(info) as source, target.open("xb") as output:
                created.append(target)
                shutil.copyfileobj(source, output)
            outputs.append({"member": info.filename, "path": f"{relative}/{target.name}",
                            "bytes": info.file_size})
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return {"directory": relative, "files": outputs}


def process(root, source, receipt, action, member, members, offset, limit):
    initial = source.lstat()
    if not regular_file(initial):
        raise PermissionError("QQ ZIP 必须为普通文件")
    try:
        with source.open("rb") as archive_file:
            info = os.fstat(archive_file.fileno())
            if not regular_file(info) or file_identity(info) != receipt:
                raise PermissionError("QQ ZIP 自导入后发生过变更，请重新收取原附件")
            if info.st_size > MAX_BYTES:
                raise ValueError("QQ ZIP 超过 128 MiB")
            with zipfile.ZipFile(archive_file) as archive:
                items = archive.infolist()
                if len(items) > MAX_MEMBERS:
                    raise ValueError("QQ ZIP 条目超过 10000 项")
                counts = Counter(item.filename.casefold() for item in items)
                if action == "zip_list":
                    return list_page(items, counts, offset, limit)
                if action == "zip_read":
                    return read_text(archive, pick(archive, member, counts), offset, limit)
                if action == "zip_material":
                    selected = pick(archive, member, counts)
                    if selected.file_size > limit * 4 + 3:
                        raise ValueError("会议材料太长，请缩小选材")
                    content = archive.read(selected).decode("utf-8-sig")
                    if len(content) > limit:
                        raise ValueError("会议材料太长，请缩小选材")
                    return {"text": content}
                names = json.loads(members) if members else [member]
                if (not isinstance(names, list) or not 1 <= len(names) <= 100
                        or not all(isinstance(name, str) and name for name in names)
                        or len(set(names)) != len(names)):
                    raise ValueError("请一次选择 1 至 100 个互不重复的 ZIP 条目")
                return stage(root, archive, [pick(archive, name, counts) for name in names])
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError) as error:
        raise ValueError("QQ ZIP 已损坏，或所选条目使用不支持的压缩方式") from error
