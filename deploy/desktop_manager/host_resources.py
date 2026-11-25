"""Read a short, current resource snapshot on the host running the controller."""

import os
from pathlib import Path
from time import time

import psutil


def snapshot(project):
    cpu = psutil.cpu_percent(interval=.15)
    ram = psutil.virtual_memory()
    mounts = {}
    system_drive = os.environ.get("SystemDrive")
    if system_drive:
        mounts[str(Path(system_drive + os.sep))] = "系统盘"
    project_drive = Path(project).resolve().anchor
    mounts[project_drive] = "系统盘／项目盘" if project_drive in mounts else "项目盘"

    disks = []
    for mount, label in mounts.items():
        try:
            disk = psutil.disk_usage(mount)
        except OSError:
            continue
        disks.append({"label": label, "volume": mount.rstrip("\\/") or mount,
                      "total": disk.total, "free": disk.free, "percent": disk.percent})

    processes = []
    for process in psutil.process_iter(("pid", "name", "memory_info"), ad_value=None):
        metrics = process.info
        memory = metrics["memory_info"]
        if memory and memory.rss:
            processes.append({"pid": metrics["pid"], "name": metrics["name"] or "未知进程", "rss": memory.rss})
    processes.sort(key=lambda row: (-row["rss"], row["pid"]))
    return {"observed_at": time(), "cpu_percent": cpu,
            "memory": {"total": ram.total, "available": ram.available, "percent": ram.percent},
            "disks": disks, "processes": processes[:5]}
