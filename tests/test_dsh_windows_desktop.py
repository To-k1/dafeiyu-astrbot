import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile, ZipInfo

from deploy.dsh_bridge.windows_desktop import DesktopBroker, WindowsNote, load_personal_apps
from deploy.dsh_bridge.desktop_tools import desktop_server


class DesktopBrokerTests(unittest.TestCase):
    def test_install_registered_portable_zip_without_starting_or_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages = root / "packages"
            packages.mkdir()
            manifest = root / "personal-apps.json"
            manifest.write_text(json.dumps({
                "editor": {"executable": "editor/bin/editor.exe", "archive": "editor.zip"},
                "tool": "tool.exe",
            }), encoding="utf-8")
            archive = packages / "editor.zip"
            with ZipFile(archive, "w") as package:
                package.writestr("bin/editor.exe", b"portable executable")
                package.writestr("assets/config.txt", "setting")
            driver = Mock()
            broker = DesktopBroker(root, driver, apps=load_personal_apps(manifest),
                                   apps_root=root / "apps", packages_root=packages)
            self.assertEqual(broker.execute("app_list")["personal_apps"][0]["package_status"], "not_installed")
            with self.assertRaisesRegex(ValueError, "没有登记本地安装包"):
                broker.execute("app_install", "tool")
            with self.assertRaisesRegex(ValueError, "仅接受登记 ID"):
                broker.execute("app_install", "editor", str(archive))

            self.assertEqual(broker.execute("app_install", "editor"), {"app_id": "editor", "status": "installed"})
            executable = root / "apps/editor/bin/editor.exe"
            self.assertEqual(executable.read_bytes(), b"portable executable")
            self.assertEqual((root / "apps/editor/assets/config.txt").read_text(), "setting")
            self.assertEqual(broker.execute("app_list")["personal_apps"][0]["package_status"], "installed")
            driver.launch_app.assert_not_called()

            with ZipFile(archive, "w") as package:
                package.writestr("bin/editor.exe", b"replacement")
            self.assertEqual(broker.execute("app_install", "editor")["status"], "already_installed")
            self.assertEqual(executable.read_bytes(), b"portable executable")

    def test_bad_portable_archives_leave_no_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages = root / "packages"
            packages.mkdir()
            apps_root = root / "apps"
            broker = DesktopBroker(root, Mock(), apps={
                "editor": {"executable": "editor/editor.exe", "archive": "editor.zip"},
            }, apps_root=apps_root, packages_root=packages)
            symlink = ZipInfo("escape.txt")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            reparse = ZipInfo("junction/")
            reparse.external_attr = stat.FILE_ATTRIBUTE_REPARSE_POINT
            for bad in ("../out.txt", "..\\out.txt", "C:/out.txt", "/out.txt",
                        "editor.exe:stream", "EDITOR.EXE", symlink, reparse):
                with self.subTest(bad=bad):
                    with ZipFile(packages / "editor.zip", "w") as package:
                        package.writestr("editor.exe", b"candidate")
                        package.writestr(bad, b"invalid")
                    with self.assertRaises(ValueError):
                        broker.execute("app_install", "editor")
                    self.assertEqual(list(apps_root.iterdir()), [])
                    self.assertFalse((root / "out.txt").exists())
            with ZipFile(packages / "editor.zip", "w") as package:
                package.writestr("readme.txt", "no executable")
            with self.assertRaisesRegex(ValueError, "缺少登记的 EXE"):
                broker.execute("app_install", "editor")
            self.assertEqual(list(apps_root.iterdir()), [])
            (apps_root / "editor").mkdir()
            retained = apps_root / "editor/owner.txt"
            retained.write_text("owner", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                broker.execute("app_install", "editor")
            self.assertEqual(retained.read_text(encoding="utf-8"), "owner")

    def test_registered_personal_apps_use_a_tracked_launch_and_return_status(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            app = folder / "tool.exe"
            app.touch()
            process = SimpleNamespace(pid=123, poll=Mock(return_value=None))
            driver = SimpleNamespace(launch_app=Mock(return_value=process),
                                     inspect_app=Mock(return_value={"status": "running", "pid": 123}),
                                     close_app=Mock(return_value={"status": "kept_open", "pid": 123}))
            broker = DesktopBroker(folder, driver, apps={"tool": "tool.exe"}, apps_root=folder)
            self.assertEqual(broker.execute("app_list")["personal_apps"], [
                {"app_id": "tool", "status": "not_started"}])
            self.assertEqual(broker.execute("app_close", "tool")["status"], "not_started")
            driver.close_app.assert_not_called()
            for bad_name, text in (("../tool", ""), ("tool.exe", ""), ("tool", "--unexpected")):
                with self.subTest(name=bad_name, text=text), self.assertRaises(ValueError):
                    broker.execute("app_launch", bad_name, text)
            self.assertEqual(broker.execute("app_launch", "tool"), {
                "app_id": "tool", "status": "running", "pid": 123})
            broker.execute("app_launch", "tool")
            driver.launch_app.assert_called_once_with(app)
            self.assertEqual(broker.execute("app_close", "tool")["status"], "kept_open")
            driver.close_app.assert_called_once_with(process, app)
            self.assertEqual(broker.execute("list")["registered"], ["notepad", "tool"])

    def test_bad_paths_in_personal_app_manifest_cannot_be_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "personal-apps.json"
            self.assertEqual(load_personal_apps(manifest), {})
            for path in (r"C:\tools\app.exe", "//host/share/app.exe", "folder/../outside.exe",
                         "app.exe:stream.exe", "foo.cmd", "CON.exe"):
                with self.subTest(path=path):
                    manifest.write_text(json.dumps({"app": path}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_personal_apps(manifest)
            manifest.write_text(json.dumps({"app": "folder/app.exe"}), encoding="utf-8")
            self.assertEqual(load_personal_apps(manifest), {"app": "folder/app.exe"})
            for spec in ({"executable":"another/app.exe", "archive":"app.zip"},
                         {"executable":"app/app.exe", "archive":"../app.zip"},
                         {"executable":"app/app.exe", "archive":"https://example.invalid/app.zip"}):
                manifest.write_text(json.dumps({"app": spec}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_personal_apps(manifest)

    def test_graceful_close_only_targets_the_verified_main_window(self):
        driver = object.__new__(WindowsNote)
        image = Path("tool.exe")
        driver.user = SimpleNamespace(IsWindowVisible=Mock(return_value=True), GetWindow=Mock(return_value=0),
                                      PostMessageW=Mock(return_value=True))
        driver.require_desktop = Mock()
        driver.window_pid = Mock(return_value=123)
        driver.process_image = Mock(return_value=image)
        driver.find_windows = Mock(return_value=[100, 101])
        process = SimpleNamespace(pid=123, poll=Mock(return_value=None),
                                  wait=Mock(side_effect=subprocess.TimeoutExpired("tool", 2)))
        self.assertEqual(driver.close_app(process, image)["reason"], "no_unique_main_window")
        driver.user.PostMessageW.assert_not_called()
        driver.find_windows.return_value = [100]
        driver.user.GetWindow.return_value = 30  # modal owned by another window
        self.assertEqual(driver.close_app(process, image)["reason"], "no_unique_main_window")
        driver.user.PostMessageW.assert_not_called()
        driver.user.GetWindow.return_value = 0
        driver.window_pid.return_value = 321  # target handle now maps to a different process
        self.assertEqual(driver.close_app(process, image)["status"], "unconfirmed")
        driver.user.PostMessageW.assert_not_called()
        driver.window_pid.return_value = 123
        self.assertEqual(driver.close_app(process, image)["reason"], "app_still_running")
        driver.user.PostMessageW.assert_called_once_with(100, 0x0010, 0, 0)
        process.wait.assert_called_once_with(timeout=2)

    def test_only_registered_files_and_verified_save_with_no_force_close(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            note = folder / "dsh-test.txt"
            driver = SimpleNamespace(launch=Mock(return_value=9), screenshot=Mock(return_value="image"),
                                     type_and_save=Mock(), close=Mock(side_effect=[False, True]))
            broker = DesktopBroker(folder, driver)
            with self.assertRaises(ValueError):
                broker.execute("open", "../outside.txt")
            driver.launch.assert_not_called()
            self.assertEqual(broker.execute("open", note.name)["status"], "opened")
            with self.assertRaisesRegex(ValueError, "回读"):
                broker.execute("write", note.name, "expected")
            driver.type_and_save.side_effect = lambda _hwnd, _name, content: note.write_text(content, encoding="utf-8")
            self.assertEqual(broker.execute("write", note.name, "已保存")["status"], "saved")
            self.assertEqual(broker.execute("capture", note.name)["png"], "image")
            self.assertEqual(broker.execute("close", note.name)["status"], "kept_open")
            self.assertEqual(broker.execute("list")["notes"], [note.name])
            self.assertEqual(broker.execute("close", note.name)["status"], "closed")
            self.assertEqual(broker.execute("list")["notes"], [])


class DesktopToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_apps_install_returns_the_broker_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "desktop.jsonl"
            receipt = {"app_id": "editor", "status": "installed"}
            target = "deploy.dsh_bridge.desktop_tools.desktop_call"
            with patch(target, new=AsyncMock(return_value=receipt)) as desktop_call:
                response = await desktop_server(evidence, "token").call_tool(
                    "windows_apps", {"op": "install", "app_id": "editor"})
            desktop_call.assert_awaited_once_with("token", "app_install", "editor")
            self.assertEqual(json.loads(response[0].text), receipt)
            self.assertEqual(json.loads(evidence.read_text(encoding="utf-8")), {"action": "app_install", **receipt})
