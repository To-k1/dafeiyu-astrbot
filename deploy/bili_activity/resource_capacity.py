"""Bound local cold starts and share the media slot between Windows services."""

import asyncio
import errno
import os
from contextlib import asynccontextmanager
from pathlib import Path

import psutil


MIN_AVAILABLE_BYTES = 3 * 1024**3
MAX_WAIT_SECONDS = 90


async def wait_for_memory():
    while psutil.virtual_memory().available < MIN_AVAILABLE_BYTES:
        await asyncio.sleep(1)


def try_lock(handle):
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
    else:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


@asynccontextmanager
async def media_slot():
    # The console and the Bili companion run as the same Windows Ops account.
    location = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not location:
        yield
        return
    path = Path(location) / "FishHome/manager/media-processing.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            async with asyncio.timeout(MAX_WAIT_SECONDS):
                while True:
                    await wait_for_memory()
                    if try_lock(handle):
                        break
                    await asyncio.sleep(0.35)
        except TimeoutError:
            raise ValueError("本机媒体处理仍在排队，或可用内存低于 3 GiB，请稍后再试") from None
        yield
