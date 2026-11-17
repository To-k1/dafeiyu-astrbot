"""Durable, receipt-aware FIFO for one AstrBot OneBot account."""

import asyncio
import inspect
import json
import random
import shutil
import sqlite3
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import uuid4


class OutboxResult(RuntimeError):
    def __init__(self, entry_id, state):
        self.entry_id, self.state = entry_id, state
        super().__init__(f"QQ 出站 {state}（{entry_id}）")


class QueuedDelivery(OutboxResult):
    """The outbox accepted the request, but no send receipt is available yet."""


class DeliveryFailed(OutboxResult):
    """The request was blocked, expired or definitively rejected."""


class DeliveryUncertain(OutboxResult):
    """A send may have occurred. Do not resubmit this message."""


MEDIA_TYPES = {"image", "record", "video", "file"}
MAX_MEDIA = 16 * 1024 * 1024


class QQOutbox:
    def __init__(self, path, *, send, ready, allowed, unavailable_errors=(), rejected_errors=(),
                 first_delay=(1, 4), following_delay=(2, 5), poll_interval=2,
                 max_age=86400, send_timeout=20, clock=time.time):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.media = path.with_suffix(".media")
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
            sequence INTEGER PRIMARY KEY, entry_id TEXT NOT NULL UNIQUE, dedupe_key TEXT UNIQUE,
            action TEXT NOT NULL, params TEXT, state TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, updated REAL NOT NULL, guarded INTEGER NOT NULL,
            response TEXT, message_id TEXT, error TEXT
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS messages_state ON messages(state, sequence)")
        self.send, self.ready, self.allowed = send, ready, allowed
        self.unavailable_errors, self.rejected_errors = unavailable_errors, rejected_errors
        self.first_delay, self.following_delay = first_delay, following_delay
        self.poll_interval, self.max_age, self.clock = poll_interval, max_age, clock
        self.send_timeout = send_timeout
        self.wake = asyncio.Event()
        self.submit_lock = asyncio.Lock()
        self.worker = None
        self.guards = {}
        self.delayed = None
        self.last_sent = None
        self.closed = False
        with self.db:
            # A crash after writing "sending" can occur after OneBot accepted it.
            interrupted = self.db.execute("""UPDATE messages SET state='uncertain', params=NULL, updated=?,
                error='interrupted_send' WHERE state='sending' RETURNING entry_id""", (clock(),)).fetchall()
            stale_guarded = self.db.execute("""UPDATE messages SET state='blocked', params=NULL, updated=?,
                error='guard_expired' WHERE state='pending' AND guarded=1 RETURNING entry_id""", (clock(),)).fetchall()
        for row in interrupted + stale_guarded:
            self._clean_media(row["entry_id"])

    def start(self):
        if self.closed:
            raise RuntimeError("QQ 出站队列已关闭")
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run(), name="fishhome-qq-outbox")

    async def close(self):
        async with self.submit_lock:
            if self.closed:
                return
            self.closed = True
            if self.worker is not None:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
            for entry_id in list(self.guards):
                if self.status(entry_id)["state"] == "pending":
                    self._finish(entry_id, "blocked", error="guard_expired")
            self.guards.clear()
            self.db.close()

    def _row(self, entry_id):
        return self.db.execute("SELECT * FROM messages WHERE entry_id=?", (entry_id,)).fetchone()

    def status(self, entry_id):
        row = self._row(entry_id)
        return ({key: row[key] for key in ("entry_id", "state", "message_id", "error", "created", "updated")}
                if row else None)

    def summary(self):
        return dict(self.db.execute("SELECT state, count(*) FROM messages GROUP BY state").fetchall())

    def cancel(self, entry_id):
        row = self._row(entry_id)
        if row is None or row["state"] != "pending":
            return False
        self._finish(entry_id, "cancelled")
        self.wake.set()
        return True

    @staticmethod
    def _local_media(value):
        if not isinstance(value, str) or not value:
            return None
        local_uri = value.startswith("file:")
        if local_uri:
            uri = urlsplit(value)
            if uri.netloc.lower() not in {"", "localhost"} or uri.query or uri.fragment:
                raise ValueError("QQ 附件必须使用本机文件")
            value = url2pathname(uri.path)
        path = Path(value)
        if not path.is_absolute():
            if local_uri:
                raise ValueError("QQ 附件的本机文件路径必须是绝对路径")
            return None
        if str(path).startswith(("\\\\", "//")):
            raise ValueError("QQ 附件不能使用网络文件路径")
        return path

    def _snapshot(self, entry_id, params):
        copied = 0
        total = 0

        def visit(value):
            nonlocal copied, total
            if isinstance(value, list):
                for part in value:
                    visit(part)
            elif isinstance(value, dict):
                data = value.get("data")
                if value.get("type") in MEDIA_TYPES and isinstance(data, dict):
                    path = self._local_media(data.get("file"))
                    if path is not None:
                        info = path.lstat()
                        total += info.st_size
                        if (not stat.S_ISREG(info.st_mode) or total > MAX_MEDIA
                                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                            raise ValueError("QQ 本机附件需要是普通文件且合计不超过 16 MiB")
                        folder = self.media / entry_id / str(copied)
                        folder.mkdir(parents=True, exist_ok=True)
                        target = folder / path.name
                        with path.open("rb") as source, target.open("xb") as output:
                            # Bound the copy even if a producer grows the file during admission.
                            content = source.read(info.st_size + 1)
                            if len(content) != info.st_size:
                                raise ValueError("QQ 本机附件在加入队列期间改变")
                            output.write(content)
                        data["file"] = target.as_uri()
                        copied += 1
                for part in value.values():
                    visit(part)

        visit(params)
        return params

    def _clean_media(self, entry_id):
        folder = self.media / entry_id
        if folder.is_dir() and folder.resolve().parent == self.media.resolve() and not folder.is_symlink():
            shutil.rmtree(folder, ignore_errors=True)

    async def submit(self, action, params, *, key=None, wait=30, live_guard=None, expires_in=None):
        if not self.allowed(action, params):
            raise PermissionError("QQ 出站目标不再获准")
        async with self.submit_lock:
            self.start()
            existing = (self.db.execute("SELECT entry_id FROM messages WHERE dedupe_key=?", (key,)).fetchone()
                        if key is not None else None)
            entry_id = existing["entry_id"] if existing else uuid4().hex
            if not existing:
                now = self.clock()
                copy = json.loads(json.dumps(params, ensure_ascii=False))
                try:
                    copy = self._snapshot(entry_id, copy)
                    with self.db:
                        self.db.execute("""INSERT INTO messages
                            (entry_id, dedupe_key, action, params, state, created, expires, updated, guarded)
                            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                            (entry_id, key, action, json.dumps(copy, ensure_ascii=False), now,
                             now + (self.max_age if expires_in is None else expires_in), now, int(live_guard is not None)))
                except (OSError, TypeError, ValueError):
                    self._clean_media(entry_id)
                    raise DeliveryFailed(entry_id, "attachment_invalid") from None
                if live_guard is not None:
                    self.guards[entry_id] = live_guard
            self.wake.set()
        try:
            return await self.wait(entry_id, timeout=wait)
        finally:
            if live_guard is not None and self.guards.get(entry_id) is live_guard:
                self.guards.pop(entry_id, None)
                if not self.closed and self.status(entry_id)["state"] == "pending":
                    self._finish(entry_id, "blocked", error="guard_expired")

    async def wait(self, entry_id, *, timeout=30):
        deadline = time.monotonic() + timeout
        while not self.closed:
            row = self._row(entry_id)
            if row is None:
                raise ValueError("QQ 出站队列中找不到该记录")
            state = row["state"]
            if state == "sent":
                return json.loads(row["response"])
            if state == "uncertain":
                raise DeliveryUncertain(entry_id, state)
            if state not in {"pending", "sending"}:
                raise DeliveryFailed(entry_id, state)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QueuedDelivery(entry_id, state)
            await asyncio.sleep(min(.05, remaining))
        raise QueuedDelivery(entry_id, "pending")

    def _finish(self, entry_id, state, *, error=None, response=None, message_id=None):
        with self.db:
            self.db.execute("""UPDATE messages SET state=?, updated=?, params=NULL, error=?, response=?, message_id=?
                WHERE entry_id=?""", (state, self.clock(), error, response, message_id, entry_id))
        self._clean_media(entry_id)

    async def _run(self):
        while True:
            self.wake.clear()
            if await self._step():
                continue
            delay = self.poll_interval
            if self.delayed is not None:
                delay = min(delay, max(0, self.delayed[1] - self.clock()))
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except TimeoutError:
                continue

    async def _step(self):
        row = self.db.execute("SELECT * FROM messages WHERE state='pending' ORDER BY sequence LIMIT 1").fetchone()
        if row is None:
            self.delayed = None
            return False
        entry_id, action, params = row["entry_id"], row["action"], json.loads(row["params"])
        now = self.clock()
        if now >= row["expires"]:
            self._finish(entry_id, "expired")
            return True
        if (row["guarded"] and entry_id not in self.guards) or not self.allowed(action, params):
            self._finish(entry_id, "blocked", error="not_authorized")
            return True
        try:
            online = await self.ready(action, params)
        except Exception:
            online = False
        if self.status(entry_id)["state"] != "pending":
            return True
        if not online:
            self.delayed = None
            if row["guarded"] and self.status(entry_id)["state"] == "pending":
                self._finish(entry_id, "blocked", error="offline")
                return True
            return False

        now = self.clock()
        if self.delayed is None or self.delayed[0] != entry_id:
            following = self.last_sent is not None and now - self.last_sent <= self.following_delay[1]
            interval = self.following_delay if following else self.first_delay
            self.delayed = (entry_id, (self.last_sent if following else now) + random.uniform(*interval))
        if now < self.delayed[1]:
            return False

        # Revalidate immediately before crossing the OneBot boundary.
        if now >= row["expires"]:
            self._finish(entry_id, "expired")
            return True
        guard = self.guards.get(entry_id)
        if row["guarded"]:
            permitted = False
            if guard is not None:
                try:
                    permitted = guard()
                    if inspect.isawaitable(permitted):
                        permitted = await permitted
                except Exception:
                    permitted = False
            if not permitted or self.guards.get(entry_id) is not guard:
                if self.status(entry_id)["state"] == "pending":
                    self._finish(entry_id, "blocked", error="guard_expired")
                return True
        if self.status(entry_id)["state"] != "pending":
            return True
        if not self.allowed(action, params):
            self._finish(entry_id, "blocked", error="not_authorized")
            return True
        if self.clock() >= row["expires"]:
            self._finish(entry_id, "expired")
            return True
        with self.db:
            changed = self.db.execute("UPDATE messages SET state='sending', updated=? "
                                      "WHERE entry_id=? AND state='pending'", (now, entry_id)).rowcount
        if not changed:
            return True
        self.delayed = None
        return await self._send(entry_id, action, params)

    async def _send(self, entry_id, action, params):
        try:
            async with asyncio.timeout(self.send_timeout):
                reply = await self.send(action, **params)
        except self.unavailable_errors:
            with self.db:
                self.db.execute("UPDATE messages SET state='pending', updated=? WHERE entry_id=?",
                                (self.clock(), entry_id))
            return False
        except self.rejected_errors as exc:
            self.last_sent = self.clock()
            self._finish(entry_id, "rejected", error=str(getattr(exc, "retcode", "")) or type(exc).__name__)
            return True
        except asyncio.CancelledError:
            self._finish(entry_id, "uncertain", error="interrupted_send")
            raise
        except Exception as exc:
            self.last_sent = self.clock()
            self._finish(entry_id, "uncertain", error=type(exc).__name__)
            return True

        self.last_sent = self.clock()
        data = reply.get("data", reply) if isinstance(reply, dict) else {}
        if isinstance(reply, dict) and (reply.get("status") in {"failed", "error"}
                                       or reply.get("retcode", 0) not in (0, None)):
            self._finish(entry_id, "rejected", error=str(reply.get("retcode", "platform_rejected")))
            return True
        value = data.get("message_id") if isinstance(data, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, str)) or str(value) in {"0", ""}:
            self._finish(entry_id, "uncertain", error="missing_message_id")
            return True
        self._finish(entry_id, "sent", response=json.dumps(reply, ensure_ascii=False), message_id=str(value))
        return True
