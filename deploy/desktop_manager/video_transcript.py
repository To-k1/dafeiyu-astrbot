"""Durable, bounded Bilibili transcript tasks owned by the existing public queue."""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from time import time

import psutil

from deploy.bili_activity.bili_api import BiliAPI
from deploy.bili_activity.media import _subtitle_rows, download_assets, find_ffmpeg
from deploy.bili_activity.resource_capacity import media_slot
from deploy.dsh_bridge.media_asr import transcribe_audio
from deploy.dsh_bridge.public_video import video_reference
from .windows import write_json


BLOCK_SECONDS = 60
MEDIA_TTL = 45 * 60
TRANSCRIPT_MODES = ("ocr", "audio", "ocr_audio")
ARTIFACTS = ("captions.txt", "captions.srt", "speech.txt", "speech.srt")
IDENT = re.compile(r"[0-9a-f]{32}")


def media_folder(queue, ident):
    if not IDENT.fullmatch(ident):
        raise ValueError("无效的媒体任务编号")
    return queue / "media-temp" / ident


def remove_media(queue, ident):
    folder = media_folder(queue, ident)
    if folder.is_dir() and folder.resolve() == queue.resolve() / "media-temp" / ident:
        shutil.rmtree(folder)


def purge_scratch(queue):
    root = queue.resolve() / "media-temp"
    for lease in root.glob("*"):
        if not IDENT.fullmatch(lease.name) or lease.resolve() != root / lease.name:
            continue
        for folder in lease.glob("fishhome-asr-*"):
            if folder.is_dir() and folder.resolve() == lease / folder.name:
                try:
                    shutil.rmtree(folder)
                except OSError:
                    continue


def purge_media(queue, protected=()):
    for folder in (queue / "media-temp").glob("*"):
        if IDENT.fullmatch(folder.name) and folder.name not in protected:
            try:
                if time() - folder.stat().st_mtime > MEDIA_TTL:
                    remove_media(queue, folder.name)
            except OSError:
                continue


def transcript_dir(queue, ident):
    if not IDENT.fullmatch(ident):
        raise ValueError("无效的媒体任务编号")
    return queue / "transcripts" / ident


def clock(seconds):
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def artifact_refs(path):
    return {name: f"transcripts/{path.name}/{name}" for name in ARTIFACTS if (path / name).is_file()}


def render_artifacts(out, checkpoint):
    for tag in ("captions", "speech"):
        rows = []
        for block in checkpoint["blocks"]:
            for row in block.get(tag, []):
                if rows and row["start"] <= rows[-1]["end"] + .05 and row["text"] == rows[-1]["text"]:
                    rows[-1]["end"] = max(row["end"], rows[-1]["end"])
                else:
                    rows.append(dict(row))
        if not rows:
            continue
        formats = {
            "txt": "\n".join(f"[{clock(row['start'])}–{clock(row['end'])}] {row['text']}" for row in rows) + "\n",
            "srt": "\n".join(f"{i}\n{clock(row['start'])} --> {clock(row['end'])}\n{row['text']}\n"
                             for i, row in enumerate(rows, 1)),
        }
        for ext, text in formats.items():
            path = out / f"{tag}.{ext}"
            pending = path.with_suffix(".tmp")
            pending.write_text(text, encoding="utf-8")
            pending.replace(path)


def report_text(out):
    sections = []
    for name, label in (("captions.txt", "画面字幕"), ("speech.txt", "原音转写")):
        if (out / name).is_file():
            sections.append("## " + label + "\n\n" + (out / name).read_text(encoding="utf-8"))
    return "\n".join(sections)


def speech_rows(clip, result):
    start, end = clip["start"], clip["end"]
    rows = []
    for sentence in result.get("sentences", []):
        left, right, text = (sentence.get(key) for key in ("begin_time", "end_time", "text"))
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)) or not text:
            continue
        left, right = max(start, start + left / 1000), min(end, start + right / 1000)
        if left < right:
            rows.append({"start": left, "end": right, "text": text})
    if rows and "".join("".join(row["text"].split()) for row in rows) == "".join(result["text"].split()):
        return rows
    return [{"start": start, "end": end, "text": result["text"]}]


async def cpu_window(mode, ffmpeg, path, start, end, option, runtime):
    extra = dict(os.environ)
    if runtime.is_dir():
        extra["PYTHONPATH"] = str(runtime) + os.pathsep + extra.get("PYTHONPATH", "")
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-B", "-m", "deploy.bili_activity.transcript_cpu",
        mode, ffmpeg, str(path), str(start), str(end), str(option),
        env=extra, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        stdout, _ = await asyncio.wait_for(child.communicate(), 240)
    except BaseException:
        try:
            for process in psutil.Process(child.pid).children(recursive=True):
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    if child.returncode:
        raise ValueError("本地字幕或 VAD 工作进程失败，请检查 OCR/ONNX 运行环境")
    result = next((line.removeprefix(b"FISHHOME_RESULT ") for line in reversed(stdout.splitlines())
                   if line.startswith(b"FISHHOME_RESULT ")), None)
    if result is None:
        raise ValueError("本地媒体工作进程未交付处理结果")
    return json.loads(result)


async def run_transcript(spec, *, queue, ident, lease_id, runtime, audio_options, progress, should_pause, api=None):
    api = api or BiliAPI()
    bvid, page = video_reference(spec["reference"], None)
    identity = await api.identity(bvid, allow_parts=True, page_number=page, authenticated=False)
    if not identity.get("cid"):
        raise ValueError("请在 B 站链接里指定分 P 后处理字幕")
    cid = identity["cid"]
    length = float(identity.get("duration") or 0)
    start = spec.get("start", 0.0)
    end = min(length, spec.get("end", length))
    if not start < end:
        raise ValueError("请求时段超出了分 P 的时长")
    source = f"https://www.bilibili.com/video/{bvid}?p={identity['page']}"
    mode = spec["asr_mode"]
    out = transcript_dir(queue, ident)
    out.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out / "checkpoint.json"
    checkpoint = {"source": source, "cid": cid, "start": start, "end": end, "blocks": [],
                  "fetches": {"video": 0, "audio": 0}, "pending_asr": None}
    if checkpoint_path.is_file():
        previous = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if any(previous[field] != checkpoint[field] for field in ("source", "cid", "start", "end")):
            raise ValueError("该任务的来源、分 P 或时段与先前的处理记录不一致")
        checkpoint = previous

    def save():
        write_json(checkpoint_path, checkpoint)

    stored_end = checkpoint["blocks"][-1]["end"] if checkpoint["blocks"] else start
    completion, reason = "completed", ""
    if checkpoint.get("pending_asr"):
        completion, reason = "needs_review", "上一段原音请求的回执未确认，保留已完成内容"
    elif (checkpoint.get("partial_block") or {}).get("evidence", {}).get("asr"):
        completion, reason = "partial", "原音处理在区间中断，已保留收到的转写"
    cache = media_folder(queue, lease_id)
    try:
        if completion == "completed":
            progress("reading_subtitles")
            try:
                payload = await api.read("subtitles", {"bvid": bvid, "cid": cid})
                # Bili returns the same track body for the whole part. Slice it locally per block.
            except (OSError, RuntimeError, ValueError):
                payload = {}
            has_subtitles = bool(_subtitle_rows(payload, start, end))
            ffmpeg = find_ffmpeg()
            if not ffmpeg and (mode != "ocr" or not has_subtitles):
                raise ValueError("需要先安装 FFmpeg")
            while stored_end < end:
                if should_pause():
                    completion, reason = "paused", "已保存进度，可以从当前时刻继续"
                    break
                right = min(stored_end + BLOCK_SECONDS, end)
                external = _subtitle_rows(payload, stored_end, right)
                captions_from_track = mode != "audio" and has_subtitles
                kinds = []
                if mode != "audio" and not captions_from_track:
                    kinds.append("video")
                if mode in ("audio", "ocr_audio"):
                    kinds.append("audio")
                track = {"start": stored_end, "end": right, "captions": [], "speech": [], "evidence": {}}
                observed = right
                cache.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="fishhome-asr-", dir=cache) as temp:
                    folder = Path(temp)
                    speech = None
                    async with media_slot():
                        progress("fetching_media")
                        receipt = {"notes": [], "downloaded_bytes": 0}
                        before = {kind for kind in kinds if (cache / f"{bvid}-{int(cid)}-32-{kind}.m4s").is_file()}
                        paths = await download_assets(api, bvid, cid, cache, kinds, receipt, quality=32, reusable=True)
                        if cache.is_dir():
                            os.utime(cache, None)
                        for kind in paths.keys() - before:
                            checkpoint["fetches"][kind] += 1
                        save()
                        if set(kinds) - paths.keys():
                            completion, reason = "partial", "所需视频或音轨无法下载，已保留此前结果"
                            break
                        if captions_from_track:
                            track["captions"] = [{"start": max(stored_end, row["from"]), "end": min(right, row["to"]),
                                                   "text": row["content"]} for row in external]
                            track["evidence"]["captions_source"] = "subtitle_track"
                        if "video" in paths:
                            progress("ocr")
                            ocr = await cpu_window("ocr", ffmpeg, paths["video"], stored_end, right,
                                                   spec.get("ocr_language", "auto"), runtime)
                            track["captions"] = ocr["lines"]
                            track["evidence"].update(captions_source="frame_ocr", sampled_frames=ocr["sampled_frames"],
                                                     ocr_observed_end=ocr["observed_end"])
                            observed = min(observed, ocr["observed_end"])
                        if "audio" in paths:
                            progress("vad")
                            speech = await cpu_window("vad", ffmpeg, paths["audio"], stored_end, right, folder, runtime)
                            track["evidence"].update(audio_observed_end=speech["observed_end"], vad_segments=len(speech["clips"]))
                            observed = min(observed, speech["observed_end"])
                    checkpoint["partial_block"] = track
                    save()
                    for clip in (speech or {}).get("clips", []):
                        progress("asr")
                        checkpoint["pending_asr"] = {"start": clip["start"], "end": clip["end"]}
                        save()
                        data = base64.b64encode((folder / clip["path"]).read_bytes()).decode("ascii")
                        result = await transcribe_audio({**clip, "mime": "audio/wav", "data": data}, **audio_options)
                        receipt = {k: result.get(k) for k in ("status", "model", "language", "usage", "request_id", "sentences")}
                        track["evidence"].setdefault("asr", []).append(receipt)
                        if result["status"] == "unconfirmed":
                            completion, reason = "needs_review", "已发听写请求，回执未确认"
                            break
                        checkpoint["pending_asr"] = None
                        if result["status"] == "transcribed":
                            track["speech"].extend(speech_rows(clip, result))
                        elif result["status"] != "empty":
                            completion, reason = "partial", ("百炼免费额度已用完" if result["status"] ==
                                                            "free_quota_exhausted" else "百炼拒绝听写请求")
                            break
                        save()
                if completion != "completed":
                    save()
                    break
                if observed < right - .05:
                    completion, reason = "partial", "下载的媒体未覆盖完整请求时段"
                    right = max(stored_end, observed)
                    track["end"] = right
                checkpoint["blocks"].append(track)
                checkpoint.pop("partial_block", None)
                stored_end = right
                save()
                if completion != "completed":
                    break
    except ValueError as exc:
        if "3 GiB" in str(exc):
            completion, reason = "paused", "本机媒体资源暂时不足，进度已保存"
        elif checkpoint["blocks"] or checkpoint.get("partial_block"):
            completion, reason = "partial", str(exc)
        else:
            raise
    except (OSError, RuntimeError, TimeoutError) as exc:
        if not checkpoint["blocks"] and not checkpoint.get("partial_block"):
            raise
        completion, reason = "partial", f"处理当前区间时遇到 {type(exc).__name__}，已保留结果"
    finally:
        blocks = checkpoint["blocks"] + ([checkpoint["partial_block"]] if checkpoint.get("partial_block") else [])
        render_artifacts(out, {**checkpoint, "blocks": blocks})
        if completion != "paused":
            try:
                remove_media(queue, lease_id)
            except OSError:
                pass  # The queue sweep will retry if Windows still holds a media file open.
    refs = artifact_refs(out)
    line = (f"B 站 {bvid} P{identity['page']} 已处理 {start:g}–{stored_end:g} 秒，"
            f"请求结束位置 {end:g} 秒。"+(f" {reason}。" if reason else ""))
    if not refs and completion == "completed":
        line += "本次未取得可保存的字幕或转写文字。"
    sources = {row["evidence"].get("captions_source") for row in blocks}
    if "subtitle_track" in sources:
        line += " 字幕来自 B 站外挂字幕轨。"
    if "frame_ocr" in sources:
        frames = sum(row["evidence"].get("sampled_frames", 0) for row in blocks)
        line += f" 画面字幕来自本地 OCR，共采样 {frames} 帧。"
    return {"kind": "dsh_video", "worker_route": "local_transcript", "task_id": ident, "session_id": ident,
            "completion": completion, "summary": line, "artifacts": refs,
            "has_full_report": bool(refs), "billing": [],
            "evidence": [{"source": source, "cid": cid, "sampling_range": {"start": start, "end": stored_end},
                          "requested_end": end, "blocks": len(checkpoint["blocks"]), "downloads": checkpoint["fetches"]}]}
