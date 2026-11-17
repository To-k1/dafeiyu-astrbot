"""Durable local queue for the existing public-video and search workers."""

import asyncio
import json
import logging
import re
from pathlib import Path
from time import time
from traceback import extract_tb

from deploy.dsh_bridge.public_search import validate_query
from deploy.dsh_bridge.public_video import video_reference

from .windows import write_json


JOB_ID = re.compile(r"[0-9a-f]{32}")
PUBLIC_JOBS_PATH = r"/api/worker/public-jobs/{ident:[0-9a-f]{32}}"
WORKER_BODY_LIMIT = 512_000  # Includes JSON escapes for multilingual persona and work guides.
ACTIVE = {"running", "cancelling"}
UNKNOWN = {"code": "worker_interrupted", "detail": "工作进程已中断，未确认结果。"}
logger = logging.getLogger(__name__)


class PublicJobUnavailable(ValueError):
    pass


def validate_agent(agent):
    def text(value, limit):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= limit

    if (not isinstance(agent, dict) or agent.keys() != {"id", "name", "version", "persona", "guides"}
            or not isinstance(agent["id"], str) or not re.fullmatch(r"[0-9a-f]{16}", agent["id"])
            or not text(agent["name"], 60) or not text(agent["persona"], 16000)
            or type(agent["version"]) is not int or agent["version"] < 1
            or not isinstance(agent["guides"], list) or len(agent["guides"]) > 8):
        raise ValueError("小弟身份或人格快照无效")
    for guide in agent["guides"]:
        if (not isinstance(guide, dict) or guide.keys() != {"id", "title", "version", "text"}
                or not text(guide["id"], 64) or not text(guide["title"], 100)
                or type(guide["version"]) is not int or guide["version"] < 1
                or not text(guide["text"], 24000)):
            raise ValueError("工作指南快照无效")
    if sum(len(guide["text"]) for guide in agent["guides"]) > 24000:
        raise ValueError("工作指南正文合计不能超过 24000 字")
    return agent


def validate_public_job(body):
    if not isinstance(body, dict):
        raise ValueError("需要公开资料任务对象")
    kind = body.get("kind")
    fields = body.keys() - {"agent"}
    if kind == "web" and fields == {"kind", "query"}:
        spec = {"kind": kind, "query": validate_query(body["query"])}
    elif kind == "video" and fields == {"kind", "reference", "question"}:
        reference, question = body["reference"], body["question"]
        if not isinstance(reference, str) or not 1 <= len(reference) <= 500:
            raise ValueError("请提供完整 BV 号或 bilibili.com/video/ 链接")
        video_reference(reference, None)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的代看问题")
        spec = {"kind": kind, "reference": reference, "question": question.strip()}
    else:
        raise ValueError("只接受公开视频参考链接、问题或公开网页搜索问题")
    if "agent" in body:
        spec["agent"] = validate_agent(body["agent"])
    return spec


class PublicJobs:
    def __init__(self, directory, execute, *, parallel=2):
        self.directory = directory
        self.execute = execute
        self.parallel = parallel
        self._queue = asyncio.Queue()
        self._workers = []
        self._active = {}
        self._closing = False

    def _path(self, ident):
        if not isinstance(ident, str) or not JOB_ID.fullmatch(ident):
            raise ValueError("公开资料任务 ID 无效")
        return self.directory / (ident + ".json")

    def _load(self, ident):
        path = self._path(ident)
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            row = None
        if (not isinstance(row, dict) or row.get("id") != ident or not isinstance(row.get("state"), str)
                or (row["state"] == "queued" and
                    (not isinstance(row.get("created"), (int, float)) or not isinstance(row.get("body"), dict)))):
            return {"id": ident, "state": "needs_review",
                    "failure": {"code": "unreadable_record", "detail": "任务记录无法读取，需人工核对。"}}
        return row

    def _save(self, row, **changes):
        row.update(changes, updated=time())
        write_json(self._path(row["id"]), row)

    @staticmethod
    def _status(row):
        return {key: row[key] for key in ("id", "state", "created", "updated", "report", "failure") if key in row}

    def get(self, ident):
        row = self._load(ident)
        return self._status(row) if row is not None else {"id": ident, "state": "not_found"}

    def submit(self, ident, body, *, enabled=lambda _kind: True):
        existing = self._load(ident)
        body = validate_public_job(body)
        if existing is not None:
            if existing["state"] == "cancelled" and "body" not in existing:
                return self._status(existing)
            if body != existing.get("body"):
                raise ValueError("该任务 ID 已对应另一项任务")
            return self._status(existing)
        if self._closing or not enabled(body["kind"]):
            raise PublicJobUnavailable("该公开资料工作进程当前不可用")
        self.directory.mkdir(parents=True, exist_ok=True)
        row = {"id": ident, "state": "queued", "created": time(), "body": body}
        self._save(row)
        self._queue.put_nowait(ident)
        return self._status(row)

    def cancel(self, ident):
        row = self._load(ident)
        if row is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            row = {"id": ident, "state": "cancelled", "created": time()}
            self._save(row)
            return self._status(row)
        state = row["state"]
        if state == "queued":
            self._save(row, state="cancelled")
        elif state == "running":
            self._save(row, state="cancelling")
        if state in {"queued", "running"}:
            task = self._active.get(ident)
            if task:
                task.cancel()
        return self._status(row)

    def start(self):
        queued = []
        for path in self.directory.glob("*.json"):
            if not JOB_ID.fullmatch(path.stem):
                continue
            row = self._load(path.stem)
            if row is None:
                continue
            if row["state"] in ACTIVE:
                self._save(row, state="needs_review", failure=UNKNOWN)
            elif row["state"] == "queued":
                queued.append((row["created"], row["id"]))
        for _, ident in sorted(queued):
            self._queue.put_nowait(ident)
        self._workers = [asyncio.create_task(self._consume()) for _ in range(self.parallel)]

    async def close(self):
        self._closing = True
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def _consume(self):
        while not self._closing:
            ident = await self._queue.get()
            task = asyncio.create_task(self._run(ident))
            self._active[ident] = task
            try:
                await task
            except asyncio.CancelledError:
                if self._closing:
                    raise
            except OSError:
                logger.exception("Could not store public DSH job status: %s", ident)
            finally:
                self._active.pop(ident, None)
                self._queue.task_done()

    async def _run(self, ident):
        row = self._load(ident)
        if row is None or row["state"] != "queued":
            return

        def started():
            self._save(row, state="running")

        try:
            report = await self.execute(row["body"], started)
            expected = {"video": "dsh_video", "web": "dsh_web_search"}[row["body"]["kind"]]
            if not isinstance(report, dict) or report.get("kind") != expected:
                raise ValueError("DSH report kind does not match job")
        except asyncio.CancelledError:
            current = self._load(ident)
            if current is not None and current["state"] in ACTIVE:
                if self._closing or current["state"] != "cancelling":
                    self._save(current, state="needs_review", failure=UNKNOWN)
                else:
                    self._save(current, state="cancelled")
            raise
        except Exception as exc:
            current = self._load(ident)
            if current is None or current["state"] == "cancelled":
                return
            if current["state"] == "cancelling":
                self._save(current, state="needs_review", failure=UNKNOWN)
                return
            if isinstance(exc, PublicJobUnavailable):
                failure = {"code": "unavailable", "detail": "该公开资料工作进程当前不可用。"}
            elif isinstance(exc, TimeoutError):
                failure = {"code": "timed_out", "detail": "任务在执行期限内未交付结果。"}
            else:
                failure = {"code": "worker_failed", "detail": "公开资料工作进程未交付可核实结果。"}
                frames = [(Path(frame.filename).name, frame.name, frame.lineno)
                          for frame in extract_tb(exc.__traceback__)[-7:]]
                logger.warning("Public DSH job %s failed with %s at %s", ident, type(exc).__name__, frames)
            self._save(current, state="failed", failure=failure)
        else:
            self._save(row, state="completed", report=report)
