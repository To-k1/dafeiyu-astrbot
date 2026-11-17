import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from mcp.types import ImageContent, TextContent

from deploy.dsh_bridge import public_video


BVID = "BV1g8Wq6DEXj"


class PublicVideoTests(unittest.IsolatedAsyncioTestCase):
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

    def test_no_other_hosts_or_conflicting_part_selectors(self):
        with self.assertRaises(ValueError):
            public_video.video_reference(f"https://example.com/video/{BVID}", None)
        with self.assertRaises(ValueError):
            public_video.video_reference(f"https://www.bilibili.com/video/{BVID}?p=2", 1)
