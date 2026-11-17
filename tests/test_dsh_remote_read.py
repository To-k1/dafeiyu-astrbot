import unittest
from unittest.mock import AsyncMock, patch

import sys
import types

# The Bridge client is supplied by the upstream plugin at runtime.
bridge_client = types.ModuleType("deploy.dsh_bridge.dsh_client")
class DshError(Exception):
    pass
class DshClient:
    def __init__(self, config):
        pass
bridge_client.DshError = DshError
bridge_client.DshClient = DshClient
with patch.dict(sys.modules, {bridge_client.__name__: bridge_client}):
    from deploy.dsh_bridge.dsh_remote import RemoteDshClient


class RemoteFileReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_all_pages_with_line_boundaries_and_trailing_newline(self):
        content = "\n".join(f"line {i} " + "x" * 110 for i in range(250)) + "\n"
        lines = content.splitlines()
        client = RemoteDshClient()
        async def read_page(method, payload, timeout):
            offset = payload["_offset"]
            page = lines[offset - 1:offset - 1 + 200]
            return {"offset": offset, "text": "\n".join(page), "lines": len(page),
                    "eof": offset - 1 + len(page) >= len(lines), "version": "v1",
                    "bytes": len(content.encode())}
        client._call_once = AsyncMock(side_effect=read_page)
        result = await client.call("workspaceFiles.read", {"sessionId": "old-session", "path": "old/file.txt"})
        self.assertEqual(result["text"], content)
        self.assertEqual([call.args[1]["_offset"] for call in client._call_once.await_args_list], [1, 201])
        self.assertTrue(all(call.args[1]["sessionId"] == "old-session" for call in client._call_once.await_args_list))

    async def test_rejects_changed_file_between_pages(self):
        client = RemoteDshClient()
        client._call_once = AsyncMock(side_effect=[
            {"offset": 1, "text": "first", "lines": 1, "eof": False, "version": "v1"},
            {"offset": 2, "text": "second", "lines": 1, "eof": True, "version": "v2"},
        ])
        with self.assertRaises(DshError):
            await client.call("workspaceFiles.read", {"sessionId": "s", "path": "x"})
