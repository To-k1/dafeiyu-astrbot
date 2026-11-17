import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from deploy.dsh_bridge.local_video import find_ffmpeg


FFMPEG = find_ffmpeg()


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class LocalVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_returns_real_frame_paths_and_keeps_data_local(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run([
                FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=5",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=5", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                "-c:v", "mpeg4", str(workspace / "sample.mp4"),
            ], check=True, capture_output=True, timeout=20)
            params = StdioServerParameters(command=sys.executable, args=[
                "-B", "-m", "deploy.dsh_bridge.local_video", "--workspace", str(workspace), "--ffmpeg", FFMPEG,
            ])
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                rejected = await session.call_tool("sample_local_video", {"filename": "../sample.mp4"})
                self.assertTrue(rejected.isError)
                result = await session.call_tool("sample_local_video", {"filename": "sample.mp4", "start": 0, "end": 12})
            self.assertFalse(result.isError, result.content)
            data = result.structuredContent or json.loads(result.content[0].text)
            self.assertEqual(data["sampling_range"], [0, 10])
            self.assertEqual(data["visual_analysis"], "not_run")
            self.assertEqual(data["audio_transcription"], "not_run")
            self.assertEqual(data["missing_times"], [])
            self.assertEqual([row["at"] for row in data["frames"]], [0, 3.333, 6.667])
            exported = json.dumps(data)
            for row, color in zip(data["frames"], (0, 0, 2), strict=True):
                frame = workspace / row["path"]
                with Image.open(frame) as image:
                    pixel = image.getpixel((20, 20))
                self.assertGreater(pixel[color], 180)
                self.assertNotIn(base64.b64encode(frame.read_bytes()).decode("ascii"), exported)
