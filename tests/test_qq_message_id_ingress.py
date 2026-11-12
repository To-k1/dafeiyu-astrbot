"""Exercise the FishHome patches against a temporary copy of the native OneBot adapter."""

import ast
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


REPO = Path(__file__).resolve().parents[1]
ADAPTER = Path("astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py")
PATCHES = REPO / "deploy/patches"


class OneBotMessageIdIngressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        try:
            package = distribution("astrbot")
        except PackageNotFoundError:
            self.skipTest("AstrBot 4.29.4 is not installed")
        if package.version != "4.29.4" or shutil.which("git") is None:
            self.skipTest("requires AstrBot 4.29.4 and git")

        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / ADAPTER
            copy.parent.mkdir(parents=True)
            shutil.copyfile(package.locate_file(ADAPTER), copy)
            for marker, patch in (
                ("FishHome: discard unrelated QQ events", "astrbot_4.29.4_qq_admin_ingress.patch"),
                ("FishHome: require the real OneBot message ID", "astrbot_4.29.4_qq_message_id.patch"),
            ):
                if marker not in copy.read_text(encoding="utf-8"):
                    subprocess.run(["git", "apply", str(PATCHES / patch)], cwd=folder, check=True,
                                   capture_output=True)
            tree = ast.parse(copy.read_text(encoding="utf-8"))

        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "convert_message")
        future = ast.parse("from __future__ import annotations").body[0]
        self.log = Mock()
        namespace = {"astrbot_config": {"admins_id": ["astrbot", "12345"]}, "logger": self.log}
        exec(compile(ast.Module(body=[future, method], type_ignores=[]), str(ADAPTER), "exec"), namespace)
        self.convert = namespace["convert_message"]
        self.result = SimpleNamespace(sender=SimpleNamespace(user_id="12345"))
        self.adapter = SimpleNamespace(_convert_handle_message_event=AsyncMock(return_value=self.result))
        self.private = {"post_type": "message", "message_type": "private", "sub_type": "friend",
                        "user_id": 12345, "sender": {"user_id": 12345}}

    async def test_missing_and_malformed_ids_are_rejected_before_conversion(self):
        for fields in ({}, {"message_id": None}, {"message_id": ""}, {"message_id": "None"},
                       {"message_id": True}, {"message_id": []}):
            with self.subTest(fields=fields):
                self.assertIsNone(await self.convert(self.adapter, self.private | fields))
        self.adapter._convert_handle_message_event.assert_not_awaited()
        self.log.debug.assert_not_called()
        self.assertEqual(self.log.warning.call_count, 6)

    async def test_valid_id_is_preserved_and_cannot_bypass_owner_checks(self):
        for message_id in (42, -42, "42"):
            event = self.private | {"message_id": message_id}
            self.assertIs(await self.convert(self.adapter, event), self.result)
            self.adapter._convert_handle_message_event.assert_awaited_with(event)
        for fields in ({"user_id": 999}, {"sender": {"user_id": 999}}, {"message_type": "group"}):
            self.assertIsNone(await self.convert(self.adapter, self.private | {"message_id": 42} | fields))
        self.assertEqual(self.adapter._convert_handle_message_event.await_count, 3)
