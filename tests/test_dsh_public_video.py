import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from mcp.types import ImageContent, TextContent

from deploy.dsh_bridge import public_video


BVID = "BV1g8Wq6DEXj"


class PublicVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_and_explicit_modes_control_media_even_if_model_requests_otherwise(self):
        identity = {"cid": 42, "page": 2, "title": "Fixture", "duration": 600}
        async def prepare(_api, _bvid, _cid, start, end, **_options):
            return {"covered_range": {"start": start, "end": end}, "notes": [], "frames": [], "subtitles": [],
                    "subtitle_track": "not_listed", "audio": [{"mime": "audio/mpeg", "data": "fixture"}]}

        for mode in ("preview", "off", "audio", "both"):
            with self.subTest(mode=mode), \
                    patch.object(public_video.BiliAPI, "identity", AsyncMock(return_value=identity)), \
                    patch.object(public_video, "prepare_video", AsyncMock(side_effect=prepare)) as sample, \
                    patch.object(public_video, "transcribe_audio", AsyncMock(return_value={
                        "status": "transcribed", "model": "fixture-asr", "text": "正文", "start": 10, "end": 12})) as asr:
                server = public_video.build_server(asr_key="fixture-key", asr_model="fixture-asr", asr_mode=mode)
                blocks = await server.call_tool("sample_public_video", {
                    "reference": BVID, "start": 10, "end": 120, "listen": mode in ("off", "preview")})
                receipt = json.loads(blocks[0].text)
                self.assertEqual(sample.await_args.args[3:], (10, 22 if mode == "preview" else 120))
                self.assertEqual(receipt["subtitle_track"], "not_listed")
                if mode in ("preview", "off"):
                    asr.assert_not_awaited()
                    self.assertFalse(sample.await_args.kwargs["include_audio"])
                else:
                    asr.assert_awaited_once()
                    self.assertTrue(sample.await_args.kwargs["force_audio"])
                    self.assertEqual(sample.await_args.kwargs.get("include_video", True), mode != "audio")
                if mode == "preview":
                    self.assertNotIn("next_range", receipt)
                    with self.assertRaisesRegex(Exception, "只试看一个片段"):
                        await server.call_tool("sample_public_video", {"reference": BVID, "start": 23, "end": 60})
                    sample.assert_awaited_once()

    async def test_link_alone_returns_timestamps_and_typed_images_to_dsh(self):
        identity = {"cid": 42, "page": 2, "title": "Sample"}
        evidence = {"covered_range": {"start": 0, "end": 12}, "notes": [],
                    "subtitles": [{"from": 0, "to": 5, "content": "sample subtitle"}],
                    "frames": [{"at": 4, "mime": "image/jpeg", "data": base64.b64encode(b"frame").decode()}]}
        resolve = AsyncMock(return_value=identity)
        sample = AsyncMock(return_value=evidence)
        with patch.object(public_video.BiliAPI, "identity", resolve), patch.object(public_video, "prepare_video", sample):
            server = public_video.build_server()
            blocks = await server.call_tool("sample_public_video", {"reference": f"https://www.bilibili.com/video/{BVID}?p=2", "end": 12})
        resolve.assert_awaited_once_with(BVID, allow_parts=True, page_number=2, authenticated=False)
        api = sample.await_args.args[0]
        self.assertNotIn("Cookie", api.headers())
        sample.assert_awaited_once_with(api, BVID, 42, 0, 12, include_audio=False)
        self.assertIsInstance(blocks[0], TextContent)
        self.assertEqual(json.loads(blocks[0].text)["frame_times"], [4])
        self.assertEqual(blocks[1].text, "Timestamp 4s")
        self.assertIsInstance(blocks[2], ImageContent)

    async def test_multipart_requests_need_a_choice_before_media_fetch(self):
        pages = [{"cid": 1, "page": 1}, {"cid": 2, "page": 2}]
        with patch.object(public_video.BiliAPI, "identity", AsyncMock(return_value={"cid": None, "pages": pages})), \
                patch.object(public_video, "prepare_video", AsyncMock()) as sample:
            blocks = await public_video.build_server().call_tool("sample_public_video", {"reference": BVID})
        sample.assert_not_awaited()
        self.assertEqual(json.loads(blocks[0].text), {"status": "choose_part", "bvid": BVID, "parts": pages})

    async def test_repeated_sample_does_not_refetch_frames_or_record_progress(self):
        identity = {"cid": 42, "page": 2, "title": "Sample"}
        evidence = {"covered_range": {"start": 0, "end": 12}, "notes": [],
                    "subtitles": [{"from": 0, "to": 5, "content": "sample subtitle"}], "frames": []}
        sample = AsyncMock(return_value=evidence)
        with TemporaryDirectory() as folder, \
                patch.object(public_video.BiliAPI, "identity", AsyncMock(return_value=identity)), \
                patch.object(public_video, "prepare_video", sample):
            path = Path(folder) / "evidence.jsonl"
            server = public_video.build_server(evidence_file=path)
            await server.call_tool("sample_public_video", {"reference": BVID, "page": 2, "end": 12})
            repeat = await server.call_tool("sample_public_video", {
                "reference": f"https://www.bilibili.com/video/{BVID}?p=2", "end": 12})
            self.assertEqual(json.loads(repeat[0].text)["status"], "already_sampled")
            self.assertEqual(sample.await_count, 1)
            self.assertEqual(len(path.read_text(encoding="utf8").splitlines()), 1)

    async def test_long_video_returns_consecutive_batches_and_stops_at_part_end(self):
        identity = {"cid": 42, "page": 2, "title": "Sample", "duration": 810}
        async def prepare(_api, _bvid, _cid, start, end, **_options):
            return {"covered_range": {"start": start, "end": end}, "notes": [],
                    "subtitles": [{"from": start, "to": start + 1, "content": "sample"}], "frames": []}
        with patch.object(public_video.BiliAPI, "identity", AsyncMock(return_value=identity)), \
                patch.object(public_video, "prepare_video", AsyncMock(side_effect=prepare)) as sample:
            server = public_video.build_server()
            start, end = 100, 900
            for expected in ([400, 810], [700, 810], None):
                blocks = await server.call_tool("sample_public_video", {
                    "reference": BVID, "page": 2, "start": start, "end": end})
                receipt = json.loads(blocks[0].text)
                self.assertEqual(receipt.get("next_range"), expected)
                if expected:
                    start, end = expected
            self.assertEqual([call.args[3:] for call in sample.await_args_list],
                             [(100, 400), (400, 700), (700, 810)])
            repeat = await server.call_tool("sample_public_video", {
                "reference": BVID, "page": 2, "start": 100, "end": 500})
            receipt = json.loads(repeat[0].text)
            self.assertEqual(receipt["status"], "already_sampled")
            self.assertEqual(receipt["next_range"], [400, 500])
            self.assertEqual(sample.await_count, 3)

    def test_no_other_hosts_or_conflicting_part_selectors(self):
        with self.assertRaises(ValueError):
            public_video.video_reference(f"https://example.com/video/{BVID}", None)
        with self.assertRaises(ValueError):
            public_video.video_reference(f"https://www.bilibili.com/video/{BVID}?p=2", 1)
