"""Transcript checkpoint, source reuse and DashScope response contracts."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from deploy.bili_activity.media import download_assets
from deploy.desktop_manager.model_settings import asr_probe
from deploy.desktop_manager.video_transcript import media_folder, run_transcript, speech_rows
from plugin.astrbot_plugin_face_convert.speech_asr import recognize


class FakeBili:
    def __init__(self):
        self.downloads = 0

    async def identity(self, *args, **kwargs):
        return {"cid": 123, "page": 1, "duration": 120}

    async def read(self, *args, **kwargs):
        return {"subtitles": [], "tracks": []}

    async def request(self, *args, **kwargs):
        return {"dash": {"video": [{"id": 32, "baseUrl": "https://v.bilivideo.com/sample"}]}}

    async def download(self, source, path, *, limit):
        self.downloads += 1
        path.write_bytes(b"synthetic media")


class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_no_words_is_empty_but_other_client_errors_are_rejected(self):
        async def fake(request):
            message = ("ASR_RESPONSE_HAVE_NO_WORDS" if request.path == "/silence" else "OTHER_CLIENT_ERROR")
            return web.json_response({"code": "CLIENT_ERROR", "message": message}, status=400)

        app = web.Application()
        app.router.add_post("/{kind}", fake)
        async with TestServer(app) as server:
            model = "qwen-audio-3.1-asr-flash"
            message = await asr_probe("fixture", model, endpoint=str(server.make_url("/silence")))
            self.assertIn("连接正常", message)
            with self.assertRaisesRegex(ValueError, "HTTP 400，CLIENT_ERROR"):
                await asr_probe("fixture", model, endpoint=str(server.make_url("/bad-audio")))
            with self.assertRaisesRegex(ValueError, "HTTP 400，CLIENT_ERROR"):
                await asr_probe("fixture", "qwen3-asr-flash", endpoint=str(server.make_url("/silence")))

    async def test_preview_source_survives_pause_and_is_cleaned_after_resume(self):
        video, preview, task = "BV1g8Wq6DEXj", "a" * 32, "b" * 32
        with TemporaryDirectory() as folder:
            queue = Path(folder)
            cache = media_folder(queue, preview)
            api = FakeBili()
            await download_assets(api, video, 123, cache, ["video"], {"notes": [], "downloaded_bytes": 0},
                                  quality=32, reusable=True)
            done = []

            async def ocr(mode, ffmpeg, path, left, right, option, runtime):
                self.assertEqual(mode, "ocr")
                self.assertEqual(path.parent, cache)
                done.append((left, right))
                return {"lines": [{"start": left, "end": right, "text": f"实测 {left}"}],
                        "sampled_frames": 4, "observed_end": right}

            opts = {"queue": queue, "ident": task, "lease_id": preview, "runtime": queue,
                    "audio_options": {}, "progress": lambda phase: None, "api": api}
            spec = {"kind": "video", "reference": video, "question": "核对字幕", "asr_mode": "ocr"}
            with (
                patch("deploy.desktop_manager.video_transcript.find_ffmpeg", return_value="ffmpeg"),
                patch("deploy.desktop_manager.video_transcript.cpu_window", side_effect=ocr),
            ):
                first = await run_transcript(spec, **opts, should_pause=lambda: len(done) == 1)
                self.assertEqual(first["completion"], "paused")
                self.assertTrue(cache.is_dir())
                self.assertEqual(first["evidence"][0]["sampling_range"]["end"], 60)
                final = await run_transcript(spec, **opts, should_pause=lambda: False)
            self.assertEqual(final["completion"], "completed")
            self.assertEqual(done, [(0, 60), (60, 120)])
            self.assertEqual(api.downloads, 1)
            self.assertFalse(cache.exists())
            text = (queue / final["artifacts"]["captions.txt"]).read_text(encoding="utf-8")
            self.assertIn("实测 60", text)
            self.assertIn("00:02:00,000", text)

    async def test_external_subtitle_track_avoids_download_and_ocr(self):
        api = FakeBili()

        async def subtitles(*args, **kwargs):
            return {"subtitles": [{"from": 38, "to": 43, "content": "外挂字幕"}]}

        api.read = subtitles
        spec = {"reference": "BV1g8Wq6DEXj", "asr_mode": "ocr", "start": 40, "end": 45}
        with TemporaryDirectory() as folder:
            queue = Path(folder)
            with (
                patch("deploy.desktop_manager.video_transcript.find_ffmpeg", return_value=None),
                patch("deploy.desktop_manager.video_transcript.cpu_window") as local_processing,
            ):
                result = await run_transcript(spec, queue=queue, ident="a" * 32, lease_id="a" * 32,
                                              runtime=queue, audio_options={}, progress=lambda phase: None,
                                              should_pause=lambda: False, api=api)
            self.assertEqual(api.downloads, 0)
            local_processing.assert_not_called()
            self.assertEqual(result["completion"], "completed")
            text = (queue / result["artifacts"]["captions.srt"]).read_text(encoding="utf-8")
            self.assertIn("00:00:40,000 --> 00:00:43,000\n外挂字幕", text)

    async def test_both_asr_protocols_return_original_text_with_separate_payload_shapes(self):
        sent = []

        async def fake(request):
            body = await request.json()
            sent.append(body)
            if body["model"] == "qwen3-asr-flash":
                return web.json_response({"output": {"choices": [{"message": {"content": [{"text": "海底 garden"}],
                                              "annotations": [{"language": "zh"}]}}]}})
            return web.json_response({"output": {"text": "Audio route", "sentence": {"begin_time": 0, "end_time": 420,
                                        "text": "Audio route"}}, "usage": {"input_tokens": 42}})

        app = web.Application()
        app.router.add_post("/", fake)
        async with TestServer(app) as server:
            endpoint = str(server.make_url("/"))
            qwen = await recognize("data:audio/wav;base64,YQ==", "fixture", endpoint=endpoint, language="zh")
            audio = await recognize("data:audio/wav;base64,YQ==", "fixture", endpoint=endpoint,
                                    model="qwen-audio-3.1-asr-flash", language="en", keep_dialect=True)
        self.assertEqual(qwen["text"], "海底 garden")
        self.assertEqual(qwen["language"], "zh")
        self.assertEqual(sent[0]["parameters"]["asr_options"], {"language": "zh"})
        self.assertEqual(sent[1]["input"]["messages"][0]["content"][0]["type"], "input_audio")
        self.assertEqual(sent[1]["parameters"]["language_hints"], ["en"])
        self.assertTrue(sent[1]["parameters"]["keep_dialect"])
        self.assertEqual(audio["sentences"][0]["end_time"], 420)
        self.assertEqual(audio["usage"]["input_tokens"], 42)
        self.assertEqual(speech_rows({"start": 330, "end": 332}, audio),
                         [{"start": 330, "end": 330.42, "text": "Audio route"}])
        self.assertEqual(speech_rows({"start": 330, "end": 332}, qwen),
                         [{"start": 330, "end": 332, "text": "海底 garden"}])
