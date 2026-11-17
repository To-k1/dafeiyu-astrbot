"""Local-only frame evidence for a DSH media workspace."""

import argparse
import math
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from mcp.server.fastmcp import FastMCP

from deploy.bili_activity.media import MAX_FRAMES, MAX_SECONDS, _probe, extract_frame


def find_ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


class LocalVideo:
    def __init__(self, workspace, ffmpeg=None):
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("视频工作区必须是目录")
        self.ffmpeg = ffmpeg or find_ffmpeg()
        if not self.ffmpeg:
            raise ValueError("缺少 ffmpeg，请在启动媒体工具时指定 --ffmpeg")

    async def sample(self, filename, start, end):
        if not re.fullmatch(r"[^\\/:\x00-\x1f]{1,180}\.(?:mp4|mkv|mov|webm)", filename, re.I):
            raise ValueError("请输入视频工作区里的 MP4、MKV、MOV 或 WebM 文件名")
        source = self.workspace / filename
        if not source.is_file() or source.is_symlink() or source.resolve().parent != self.workspace:
            raise ValueError("找不到工作区中的视频文件")
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= start + MAX_SECONDS:
            raise ValueError("单次采样须是有效的 0–300 秒片段")
        duration = await _probe(self.ffmpeg, source)
        if duration is None or start >= duration:
            raise ValueError("无法确认视频长度，或片段超出视频范围")
        stop = min(end, duration)
        count = min(MAX_FRAMES, max(1, math.ceil((stop - start) / 4)))
        step = (stop - start) / count
        frames, missing, destination = [], [], None
        with TemporaryDirectory(prefix="fishhome-video-") as temp:
            output = Path(temp) / "frame.jpg"
            for index in range(count):
                at = start + index * step
                output.unlink(missing_ok=True)
                try:
                    data = await extract_frame(self.ffmpeg, source, output, at)
                except (OSError, ValueError, TimeoutError):
                    missing.append(round(at, 3))
                    continue
                if destination is None:
                    destination = Path(mkdtemp(prefix="media-evidence-", dir=self.workspace))
                saved = destination / f"frame-{index}.jpg"
                saved.write_bytes(data)
                frames.append({"at": round(at, 3), "path": saved.relative_to(self.workspace).as_posix()})
        return {"source": filename, "requested_range": [start, end], "sampling_range": [start, stop],
                "frames": frames, "missing_times": missing, "visual_analysis": "not_run", "audio_transcription": "not_run"}


def build_server(workspace, ffmpeg=None):
    video = LocalVideo(workspace, ffmpeg)
    server = FastMCP("FishHome local video")

    @server.tool()
    async def sample_local_video(filename: str, start: float = 0, end: float = 20) -> dict:
        """Extract timestamped frames into the local media workspace. Returns paths, never image data or an analysis."""
        return await video.sample(filename, start, end)

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()
    build_server(args.workspace, args.ffmpeg).run(transport="stdio")


if __name__ == "__main__":
    main()
