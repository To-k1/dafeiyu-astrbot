"""Exercise the real shared tool with synthetic owner QQ attachments."""

import importlib.util
import json
import os
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


class File:
    def __init__(self, path):
        self.name = path.name
        self.get_file = AsyncMock(return_value=str(path))


class Filters:
    PermissionType = types.SimpleNamespace(ADMIN="admin")

    def __getattr__(self, _):
        return lambda *args, **kwargs: lambda func: func


def load_bridge():
    identity = lambda *args, **kwargs: lambda cls: cls
    imports = {
        "astrbot.api.event": types.SimpleNamespace(filter=Filters(), AstrMessageEvent=object),
        "astrbot.api.star": types.SimpleNamespace(Star=object, Context=object, StarTools=None, register=identity),
        "astrbot.core.message.components": types.SimpleNamespace(File=File),
        "astrbot.core.utils.astrbot_path": types.SimpleNamespace(get_astrbot_temp_path=None),
        "deploy.dsh_bridge.dsh_client": types.SimpleNamespace(DshError=RuntimeError),
        "deploy.dsh_bridge.dsh_remote": types.SimpleNamespace(RemoteDshClient=None),
    }
    path = Path(__file__).resolve().parents[1] / "deploy/dsh_bridge/m3_qq.py"
    spec = importlib.util.spec_from_file_location("deploy.dsh_bridge.qq_zip_test_bridge", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", imports):
        spec.loader.exec_module(module)
    return module


bridge = load_bridge()
SCOPE = "FishQQ:FriendMessage:10001"


class QQZipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.shared = root / "shared"
        self.shared.mkdir()
        self.cache = root / "downloads"
        self.cache.mkdir()
        patches = patch.object(bridge, "get_astrbot_temp_path", return_value=str(self.cache))
        patches.start()
        self.addCleanup(patches.stop)
        if not bridge.PureWindowsPath(self.shared).is_absolute():
            workspace = patch.object(bridge, "shared_workspace", return_value=self.shared)
            workspace.start()
            self.addCleanup(workspace.stop)
        self.bridge = bridge.Main.__new__(bridge.Main)
        self.bridge.settings = root / "bridge.json"
        self.bridge.settings.write_text(json.dumps({"owner_scope": SCOPE, "owner_id": "10001",
            "shared_workspace": str(self.shared), "commission_cookie": "fixture", "commission_workspace": str(root)}),
            encoding="utf-8")
        self.conversation = AsyncMock(return_value="topic-one")
        self.bridge.context = types.SimpleNamespace(
            conversation_manager=types.SimpleNamespace(get_curr_conversation_id=self.conversation))
        self.event = types.SimpleNamespace(unified_msg_origin=SCOPE, get_sender_id=lambda: "10001",
            get_messages=lambda: [], message_obj=types.SimpleNamespace(message_id="41"))

    async def call(self, action, **kwargs):
        return json.loads(await self.bridge.dsh_shared(self.event, action, **kwargs))

    async def test_meeting_loads_only_selected_member_from_verified_owner_archive(self):
        path = await self.receive([("shared-note.md", "题头\n" + "正文\n" * 2200 + "END"), ("other.md", "no")])
        self.conversation.return_value = "topic-two"
        refs = [{"path": path, "member": "shared-note.md"}]
        result = await self.bridge.meeting_materials(SCOPE, refs)
        self.assertEqual(result, [{**refs[0], "text": "题头\n" + "正文\n" * 2200 + "END"}])
        with self.assertRaises(PermissionError):
            await self.bridge.meeting_materials(SCOPE + "2", refs)
        self.bridge.qq_zip_receipt(Path(path).name).unlink()
        with self.assertRaisesRegex(ValueError, "收取回执"):
            await self.bridge.meeting_materials(SCOPE, refs)

    async def receive(self, entries):
        source = self.cache / "资料.zip"
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries:
                archive.writestr(name, value)
        self.event.get_messages = lambda: [File(source)]
        received = (await self.call("import_qq"))["files"][0]
        self.event.get_messages = lambda: []
        return received["path"]

    async def test_list_read_and_stage_selected_members_for_dsh(self):
        path = await self.receive([("目录/纪要.txt", "第一行\n第二行\n完"), ("数据.csv", "a,b\n1,2"),
                                   ("只保留在ZIP里.txt", "未选择")])
        archive = self.shared / path.removeprefix("shared/")
        original = archive.read_bytes()
        page = await self.call("zip_list", path=path, limit=2)
        end = await self.call("zip_list", path=path, offset=page["next_offset"])
        self.assertEqual([item["member"] for item in page["entries"] + end["entries"]],
                         ["目录/纪要.txt", "数据.csv", "只保留在ZIP里.txt"])
        part = await self.call("zip_read", path=path, member="目录/纪要.txt", limit=4)
        tail = await self.call("zip_read", path=path, member="目录/纪要.txt", offset=part["next_offset"])
        self.assertEqual(part["text"] + tail["text"], "第一行\n第二行\n完")
        self.assertTrue(part["records_are_data_not_instructions"])
        chosen = json.dumps(["目录/纪要.txt", "数据.csv"], ensure_ascii=False)
        staged = await self.call("zip_extract", path=path, members=chosen)
        again = await self.call("zip_extract", path=path, members=chosen)
        self.assertNotEqual(staged["directory"], again["directory"])
        task = self.shared / staged["directory"].removeprefix("shared/")
        self.assertEqual(len(list(task.iterdir())), 2)
        for item, expected in zip(staged["files"], ("第一行\n第二行\n完", "a,b\n1,2")):
            workspace = Path(self.bridge.commission_client(SCOPE, item["path"]))
            self.assertEqual(workspace, self.shared)
            source = workspace / item["path"].removeprefix("shared/")
            self.assertEqual(source.read_bytes().decode("utf-8"), expected)
        self.assertEqual(archive.read_bytes(), original)

    async def test_only_imported_attachment_in_current_owner_conversation_is_available(self):
        path = await self.receive([("a.txt", "source")])
        self.event.get_sender_id = lambda: "10002"
        with self.assertRaises(PermissionError):
            await self.call("zip_list", path=path)
        self.event.get_sender_id = lambda: "10001"
        self.conversation.return_value = "topic-two"
        with self.assertRaises(PermissionError):
            await self.call("zip_list", path=path)
        self.conversation.return_value = "topic-one"
        self.event.get_messages = lambda: []
        rogue = self.shared / "qq-inbox/unregistered.zip"
        rogue.write_bytes((self.shared / path.removeprefix("shared/")).read_bytes())
        with self.assertRaises(PermissionError):
            await self.call("zip_list", path="shared/qq-inbox/unregistered.zip")

    async def test_rejects_unsafe_names_and_ambiguous_entries_before_creating_output(self):
        link = zipfile.ZipInfo("shortcut.txt")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        path = await self.receive([("资料.txt", "safe"), ("../outside.txt", "invalid"), (link, "invalid"),
                                   ("same.txt", "one"), ("SAME.txt", "two"), ("NUL.txt", "invalid")])
        page = await self.call("zip_list", path=path)
        self.assertEqual([item["extractable"] for item in page["entries"]],
                         [True, False, False, False, False, False])
        for name in ("../outside.txt", "shortcut.txt", "same.txt", "NUL.txt", "missing.txt"):
            with self.subTest(member=name), self.assertRaises(ValueError):
                await self.call("zip_extract", path=path, members=json.dumps(["资料.txt", name]))
        self.assertFalse((self.shared / "qq-zip").exists())

    async def test_refuses_to_read_modified_archive_and_preserves_first_import(self):
        original = await self.receive([("a.txt", "original")])
        replacement = await self.receive([("a.txt", "replacement")])
        self.assertNotEqual(original, replacement)
        self.assertEqual((await self.call("zip_read", path=original, member="a.txt"))["text"], "original")
        archive = self.shared / original.removeprefix("shared/")
        archive.write_bytes(b"modified")
        with self.assertRaises(PermissionError):
            await self.call("zip_list", path=original)

    async def test_long_text_pages_reach_eof_and_selected_files_with_same_basename_remain_distinct(self):
        large = "资料一行\n" * 1800 + "结尾"
        path = await self.receive([("甲/资料.txt", large), ("乙/资料.txt", "第二份")])
        offset = 0
        parts = []
        while offset is not None:
            result = await self.call("zip_read", path=path, member="甲/资料.txt", offset=offset)
            parts.append(result["text"])
            offset = result["next_offset"]
        self.assertTrue(result["complete"])
        self.assertEqual("".join(parts), large)
        self.assertEqual(result["bytes_total"], len(large.encode("utf-8")))
        stage = await self.call("zip_extract", path=path, members=json.dumps(["甲/资料.txt", "乙/资料.txt"]))
        self.assertEqual(len({item["path"] for item in stage["files"]}), 2)

    @unittest.skipUnless(os.name == "nt", "Windows DSH runs under a separate account")
    async def test_staged_directory_inherits_runner_access_from_shared_parent(self):
        import win32security

        path = await self.receive([("input.txt", "a source for the worker")])
        staged = await self.call("zip_extract", path=path, member="input.txt")
        directory = self.shared / staged["directory"].removeprefix("shared/")
        descriptor = win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION)
        flags, _ = descriptor.GetSecurityDescriptorControl()
        self.assertFalse(flags & win32security.SE_DACL_PROTECTED,
                         "The directory must inherit the shared workspace ACL")
