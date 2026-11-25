"""Durable local queue for the existing public-video and search workers."""

import asyncio
from plugin.astrbot_plugin_bili_activity.dsh_cards import validate_background
import json
import logging
import math
import re
from pathlib import Path
from time import time
from traceback import extract_tb

from deploy.dsh_bridge.public_search import validate_task_query
from deploy.dsh_bridge.public_video import ASR_MODES, video_reference
from deploy.dsh_bridge.go_relay import GoSubscriptionWaiting
from deploy.dsh_bridge.go_video_task import GoReportError

from .windows import write_json
from .video_transcript import TRANSCRIPT_MODES, purge_media, purge_scratch, remove_media, report_text, transcript_dir


JOB_ID = re.compile(r"[0-9a-f]{32}")
PUBLIC_JOBS_PATH = r"/api/worker/public-jobs/{ident:[0-9a-f]{32}}"
WORKER_BODY_LIMIT = 512_000  # Includes JSON escapes for multilingual persona and work guides.
ACTIVE = {"running", "cancelling"}
UNKNOWN = {"code": "worker_interrupted", "detail": "工作进程已中断，未确认结果。"}
REPORT_KINDS = {"video": "dsh_video", "web": "dsh_web_search", "desktop": "dsh_windows"}
logger = logging.getLogger(__name__)


class PublicJobUnavailable(ValueError):
    pass


def report_excerpt(report):
    if "full_report" not in report:
        return report
    return {**{key: value for key, value in report.items() if key != "full_report"}, "has_full_report": True}


def validate_agent(agent):
    def text(value, limit):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= limit

    if (not isinstance(agent, dict) or agent.keys() - {"background"} != {"id", "name", "version", "persona", "guides"}
            or not isinstance(agent["id"], str) or not re.fullmatch(r"[0-9a-f]{16}", agent["id"])
            or not text(agent["name"], 60) or not text(agent["persona"], 16000)
            or type(agent["version"]) is not int or agent["version"] < 1
            or not isinstance(agent["guides"], list) or len(agent["guides"]) > 8):
        raise ValueError("小弟身份或人格快照无效")
    if "background" in agent:
        validate_background(agent["background"])
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
        spec = {"kind": kind, "query": validate_task_query(body["query"])}
    elif kind == "video" and fields - {"asr_mode", "start", "end", "ocr_language", "asr_language",
                                       "preview_id"} == {"kind", "reference", "question"}:
        reference, question = body["reference"], body["question"]
        if not isinstance(reference, str) or not 1 <= len(reference) <= 500:
            raise ValueError("请提供完整 BV 号或 bilibili.com/video/ 链接")
        video_reference(reference, None)
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError("请提供不超过 1000 字的代看问题")
        spec = {"kind": kind, "reference": reference, "question": question.strip()}
        mode = body.get("asr_mode", "auto")
        if mode not in (*ASR_MODES, *TRANSCRIPT_MODES):
            raise ValueError("无效的视频处理方式")
        if mode != "auto":
            spec["asr_mode"] = mode
        if "preview_id" in body:
            if not isinstance(body["preview_id"], str) or not JOB_ID.fullmatch(body["preview_id"]):
                raise ValueError("无效的试看任务编号")
            spec["preview_id"] = body["preview_id"]
        if mode in TRANSCRIPT_MODES:
            for key in ("start", "end"):
                if key in body:
                    value = body[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("视频处理区间无效")
                    spec[key] = float(value)
            if spec.get("end", math.inf) <= spec.get("start", 0):
                raise ValueError("结束时间必须晚于起始时间")
            if "ocr_language" in body:
                if body["ocr_language"] not in ("auto", "zh", "en", "ja", "ko"):
                    raise ValueError("当前 OCR 支持中英日通用识别，也可指定韩文")
                spec["ocr_language"] = body["ocr_language"]
            if "asr_language" in body:
                code = body["asr_language"]
                if not isinstance(code, str) or (code and not re.fullmatch(r"[a-z]{2,8}", code)):
                    raise ValueError("请输入有效的语种代码，或留空自动识别")
                spec["asr_language"] = code
        elif fields & {"start", "end", "ocr_language", "asr_language"}:
            raise ValueError("结构化时间和语种用于字幕提取或原音转写")
    else:
        raise ValueError("只接受公开视频参考链接、问题或公开网页搜索问题")
    if "agent" in body:
        spec["agent"] = validate_agent(body["agent"])
    return spec


class PublicJobs:
    def __init__(self, directory, execute, *, quota_available=None, validate=validate_public_job, transcript=None):
        self.directory = directory
        self.execute = execute
        self.quota_available = quota_available
        self.validate = validate
        self.transcript = transcript
        self._active = {}
        self._started = False
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
                or (row["state"] in {"queued", "go_wait"} and
                    (not isinstance(row.get("created"), (int, float)) or not isinstance(row.get("body"), dict)))):
            return {"id": ident, "state": "needs_review",
                    "failure": {"code": "unreadable_record", "detail": "任务记录无法读取，需人工核对。"}}
        return row

    def _save(self, row, **changes):
        row.update(changes, updated=time())
        write_json(self._path(row["id"]), row)

    @staticmethod
    def _status(row):
        return {key: row[key] for key in ("id", "state", "phase", "created", "updated", "report", "failure",
                                         "pause_requested") if key in row}

    def get(self, ident):
        row = self._load(ident)
        return self._status(row) if row is not None else {"id": ident, "state": "not_found"}

    def full_report(self, ident, report):
        if report.get("worker_route") == "local_transcript":
            return report_text(transcript_dir(self.directory, ident))
        return report.get("full_report")

    def is_transcript(self, row):
        return bool(self.transcript and row.get("body", {}).get("asr_mode") in TRANSCRIPT_MODES)

    def control(self, ident, action):
        row = self._load(ident)
        if not row or not self.is_transcript(row):
            raise ValueError("只能暂停或续做字幕提取任务")
        if action == "pause":
            if row["state"] == "queued":
                self._save(row, state="paused", phase="paused")
            elif row["state"] == "running":
                self._save(row, pause_requested=True)
        elif action == "resume" and row["state"] == "paused":
            self._save(row, state="queued", phase="queued", pause_requested=False)
            if self._started:
                self._launch(ident)
        return self._status(row)

    def archive_video(self, ident, body, report):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._save({"id": ident, "created": time(), "body": body}, state="completed", report=report)

    def recent(self):
        rows = []
        for path in self.directory.glob("*.json"):
            if JOB_ID.fullmatch(path.stem):
                row = self._load(path.stem)
                if row is not None and row.get("body"):
                    rows.append(row)
        rows.sort(key=lambda row: row.get("created", 0), reverse=True)
        active = ACTIVE | {"queued", "go_wait"}
        newest = [row for row in rows if row["state"] in active]
        newest.extend([row for row in rows if row["state"] not in active][:30])
        return [{**{key: row[key] for key in ("id", "state", "phase", "created") if key in row},
                 "task": row["body"]} for row in newest]

    def submit(self, ident, body, *, enabled=lambda _kind: True):
        existing = self._load(ident)
        body = self.validate(body)
        if preview_id := body.get("preview_id"):
            prior = self._load(preview_id)
            preview = (prior or {}).get("report", {}).get("preview", {})
            if not preview or video_reference(preview["source"], None) != video_reference(body["reference"], None):
                raise ValueError("只能沿用同一工作台已完成试看且分 P 相同的素材")
            for path in self.directory.glob("*.json"):
                if path.stem == ident or not JOB_ID.fullmatch(path.stem):
                    continue
                row = self._load(path.stem)
                if row and row["state"] in {"queued", "running", "paused", "cancelling", "go_wait"} and (
                        row.get("body", {}).get("preview_id") == preview_id):
                    raise ValueError("该试看已有续做任务，完成或取消后再另开")
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
        if self._started:
            self._launch(ident)
        return self._status(row)

    def cancel(self, ident):
        row = self._load(ident)
        if row is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            row = {"id": ident, "state": "cancelled", "created": time()}
            self._save(row)
            return self._status(row)
        state = row["state"]
        if (state == "running" and row.get("body", {}).get("kind") == "desktop"
                and row.get("phase") in {"submitting_prompt", "waiting_for_result"}):
            raise ValueError("桌面工作已开始，请等操作回执")
        if state in {"queued", "go_wait", "paused"}:
            self._save(row, state="cancelled")
            if self.is_transcript(row):
                remove_media(self.directory, row["body"].get("preview_id") or ident)
        elif state == "running":
            self._save(row, state="cancelling")
        if state in {"queued", "go_wait", "running"}:
            task = self._active.get(ident)
            if task:
                task.cancel()
        return self._status(row)

    def start(self):
        if self._started or self._closing:
            return
        self._started = True
        purge_scratch(self.directory)
        purge_media(self.directory)
        queued = []
        for path in self.directory.glob("*.json"):
            if not JOB_ID.fullmatch(path.stem):
                continue
            row = self._load(path.stem)
            if row is None:
                continue
            if row["state"] in ACTIVE:
                if self.is_transcript(row) and row["state"] == "running":
                    self._save(row, state="paused", phase="paused", pause_requested=False)
                else:
                    self._save(row, state="needs_review", failure=UNKNOWN)
            elif row["state"] in {"queued", "go_wait"}:
                queued.append((row["created"], row["id"]))
        for _, ident in sorted(queued):
            self._launch(ident)

    async def maintain_media(self):
        while True:
            await asyncio.sleep(60)
            protected = set(self._active)
            for ident in self._active:
                row = self._load(ident) or {}
                if lease := row.get("body", {}).get("preview_id"):
                    protected.add(lease)
            await asyncio.to_thread(purge_media, self.directory, protected)

    async def close(self):
        self._closing = True
        running = list(self._active.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    def _launch(self, ident):
        task = asyncio.create_task(self._run(ident))
        self._active[ident] = task
        task.add_done_callback(lambda finished: self._finished(ident, finished))

    def _finished(self, ident, task):
        if self._active.get(ident) is task:
            self._active.pop(ident, None)
        if not task.cancelled() and (exc := task.exception()):
            logger.error("Could not store public DSH job status: %s", ident, exc_info=exc)

    async def _run(self, ident):
        row = self._load(ident)
        if row is None or row["state"] not in {"queued", "go_wait"}:
            return

        def started(phase=None):
            changes = {"state": "running"}
            if phase is not None:
                changes["phase"] = phase
            row.update(self._load(ident) or {})
            self._save(row, **changes)

        def should_pause():
            return (self._load(ident) or {}).get("pause_requested") is True

        started.job_id = ident

        try:
            while True:
                if row["state"] == "go_wait":
                    if not self.quota_available:
                        return
                    await self._await_quota()
                    # Keep go_wait on disk until the worker actually starts, so a restart can resume it.
                try:
                    if self.is_transcript(row):
                        report = await self.transcript(row["body"], started, ident, should_pause, self.directory)
                    else:
                        report = await self.execute(row["body"], started)
                    break
                except GoSubscriptionWaiting:
                    self._save(row, state="go_wait", phase="waiting_for_go_quota")
            expected = REPORT_KINDS[row["body"]["kind"]]
            if not isinstance(report, dict) or report.get("kind") != expected:
                raise ValueError("DSH report kind does not match job")
        except asyncio.CancelledError:
            current = self._load(ident)
            if current is not None and current["state"] in ACTIVE:
                if self._closing or current["state"] != "cancelling":
                    if self.is_transcript(current):
                        self._save(current, state="paused", phase="paused", pause_requested=False)
                    else:
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
                failure = {"code": "unavailable", "detail": str(exc) or "该工作进程当前不可用。"}
            elif isinstance(exc, GoReportError):
                failure = {"code": exc.code, "detail": str(exc), **({"evidence": exc.evidence} if exc.evidence else {})}
            elif isinstance(exc, TimeoutError):
                failure = {"code": "timed_out", "detail": "任务在执行期限内未交付结果。"}
            else:
                failure = {"code": "worker_failed", "detail": "公开资料工作进程未交付可核实结果。"}
                frames = [(Path(frame.filename).name, frame.name, frame.lineno)
                          for frame in extract_tb(exc.__traceback__)[-7:]]
                logger.warning("Public DSH job %s failed with %s at %s", ident, type(exc).__name__, frames)
            self._save(current, state="failed", failure=failure)
        else:
            if report.get("completion") == "paused":
                self._save(row, state="paused", phase="paused", pause_requested=False, report=report)
            else:
                self._save(row, state="completed", pause_requested=False, report=report)

    async def _await_quota(self):
        while True:
            await asyncio.sleep(60)
            if await self.quota_available():
                return
