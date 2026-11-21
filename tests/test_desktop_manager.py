import os
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

import psutil
from aiohttp.test_utils import TestClient, TestServer

if os.name == "nt":
    from deploy.desktop_manager.manager import Manager, create_app, qq_owner, read_settings
    from deploy.desktop_manager.windows import Credentials, write_json
    from deploy.fish_cost_ledger import Ledger
    from scripts.native_archive import LM, verify
    from plugin.astrbot_plugin_bili_activity import dsh_cards
    from plugin.astrbot_plugin_bili_activity.core import Store as ActivityStore
    from plugin.astrbot_plugin_bili_activity.dsh_roster import DshRoster
    from plugin.astrbot_plugin_bili_activity.dsh_meetings import DshMeetings
    from plugin.astrbot_plugin_bili_activity.dsh_task import assignment_prompt
    from plugin.astrbot_plugin_bili_activity.team_memory_backend import TeamMemoryBackend


@unittest.skipUnless(os.name == "nt", "Windows desktop manager")
class DesktopManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch.dict(os.environ, {"ComSpec": r"C:\Windows\System32\cmd.exe"}):
            self.manager = Manager(root / "dev", root / "state")
        self.manager.bridge_settings.parent.mkdir(parents=True)
        self.manager.activity.mkdir()
        write_json(self.manager.bridge_settings, {"session_cookie": "private-cookie"})
        write_json(self.manager.control, {"public_interactions": False})
        self.scope = "FishQQ:FriendMessage:12345"
        self.config = {
            "admins_id": ["12345", "astrbot"],
            "platform": [{"enable": True, "type": "aiocqhttp", "id": "FishQQ", "ws_reverse_port": 6199}],
            "platform_settings": {"enable_id_white_list": True, "id_whitelist": [self.scope],
                                  "wl_ignore_admin_on_group": False, "wl_ignore_admin_on_friend": False},
        }
        write_json(self.manager.astrbot_settings, self.config)
        self.client = TestClient(TestServer(create_app(self.manager, "test-token")))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.auth = {"Host": "127.0.0.1:6196", "Authorization": "Bearer test-token"}

    async def test_owner_setup_keeps_cookie_and_never_returns_it(self):
        private = await self.client.get("/api/config", headers={"Host": "127.0.0.1:6196"})
        self.assertEqual(private.status, 401)
        async with self.client.post("/api/config", headers=self.auth, json={"owner_qq": "12345"}) as result:
            self.assertEqual(result.status, 200)
        async with self.client.post("/api/config", headers=self.auth, json={"napcat_qq": "23456"}) as result:
            self.assertEqual(result.status, 200)
        self.assertEqual(self.manager.verify_qq()["session_cookie"], "private-cookie")
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual(result.status, 200)
            self.assertNotIn("private-cookie", await result.text())

    async def test_shared_meeting_page_uses_scoped_browser_session_and_lists_new_meeting(self):
        route = "/api/dsh-team/meeting"
        host = {"Host": "127.0.0.1:6196"}
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        self.config["agent_runner"] = {"runner_type": "local", "config": {"persona": {"persona_id": "fish-home-v3.0"}}}
        write_json(self.manager.astrbot_settings, self.config)
        with closing(ActivityStore(self.manager.activity)) as store:
            group = DshMeetings(store, self.scope)
            self.assertEqual((await self.client.get(route, headers=host)).status, 401)
            page = await self.client.get("/meetings", headers=host)
            self.assertIn("frame-ancestors http://127.0.0.1:59934", page.headers["Content-Security-Policy"])
            granted = await self.client.get(route, headers=self.auth)
            self.assertEqual((await granted.json())["meetings"], [])
            cookies = {"fishhome-meeting-6196": granted.cookies["fishhome-meeting-6196"].value}
            profiles = [{"id": ident * 16, "name": ident, "version": 1, "persona": "测试"} for ident in "ab"]
            ident = group.create("新会议", profiles, "interactive", 1, 0, 0, "input", style="casual")["id"]
            active = await self.client.get(route, headers=host, cookies=cookies)
            self.assertEqual((await active.json())["meetings"][0]["id"], ident)
            body = {"text": "一条主人插话", "post_id": "c" * 32}
            params = {"id": ident, "view": "test"}
            wrong_origin = {**host, "Origin": "http://127.0.0.1:59934"}
            post = await self.client.post(route, headers=wrong_origin, params=params, cookies=cookies, json=body)
            self.assertEqual(post.status, 401)
            self.assertEqual(group.get(ident)["transcript"], [])
            correct_origin = {**host, "Origin": "http://127.0.0.1:6196"}
            post = await self.client.post(route, headers=correct_origin, params=params, cookies=cookies, json=body)
            item = (await post.json())["transcript"][0]
            self.assertEqual((item["name"], item["text"]), ("主人", body["text"]))
            self.assertEqual((await self.client.get("/api/config", headers=host, cookies=cookies)).status, 401)

    async def test_shared_language_uses_owner_file_and_can_be_disabled(self):
        from plugin.astrbot_plugin_bili_activity import shared_language
        route = "/api/shared-language"
        with patch.object(shared_language, "DEFAULT", "not-bundled-test-file.txt"):
            before = await (await self.client.get(route, headers=self.auth)).json()
            self.assertEqual(before, {"enabled": False, "filename": "", "text": ""})
            posted = await self.client.post(route, headers=self.auth, json={"enabled": False})
            self.assertEqual(await posted.json(), before)
        content = "第一行\r\n另一行\n"
        posted = await self.client.post(route, headers=self.auth, json={
            "enabled": True, "filename": "新语言.txt", "text": content})
        self.assertEqual(posted.status, 200)
        self.assertIn(content, shared_language.prompt(self.manager.activity))
        posted = await self.client.post(route, headers=self.auth, json={"enabled": False})
        self.assertEqual((await posted.json())["text"], content)
        self.assertEqual(shared_language.prompt(self.manager.activity), "")
        invalid = await self.client.post(route, headers=self.auth, json={
            "enabled": True, "filename": "../elsewhere.md", "text": "试写"})
        self.assertEqual(invalid.status, 409)
        self.assertIn("TXT 或 Markdown", (await invalid.json())["error"])

    async def test_meme_dictionary_update_is_console_only(self):
        route = "/api/meme-dictionary"
        unauthorized = await self.client.post(route, headers={"Host": self.auth["Host"]})
        self.assertEqual(unauthorized.status, 401)
        self.assertEqual(await (await self.client.get(route, headers=self.auth)).json(), {"available": False})
        status = {"available": True, "entries": 12, "built_at": "定向测试"}
        with patch("deploy.desktop_manager.manager.memewiki.update", return_value=status) as update:
            self.assertEqual(await (await self.client.post(route, headers=self.auth)).json(), status)
        update.assert_called_once_with(self.manager.activity)

    async def test_role_card_roundtrip_and_background_only_update(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        store = ActivityStore(self.manager.activity)
        with closing(store.db):
            route = "/api/dsh-team/role-card"
            card = dsh_cards.pack("阿墨", "原人格", {"story.md": "起点", "shared/灯塔.md": "共见过灯塔"})
            self.assertEqual((await self.client.get(route + "?action=template",
                                                    headers={"Host": self.auth["Host"]})).status, 401)
            preview = await self.client.post(route + "?action=preview", headers=self.auth, data=card)
            self.assertEqual((await preview.json())["files"], ["story.md", "shared/灯塔.md"])
            self.assertEqual(DshRoster(store, self.scope).list(), [])
            created = await self.client.post(route + "?action=create", headers=self.auth, data=card)
            self.assertEqual(created.status, 200)
            agent_id = (await created.json())["id"]
            duplicate = await self.client.post(route + "?action=create", headers=self.auth, data=card)
            self.assertEqual(duplicate.status, 200)
            self.assertNotEqual((await duplicate.json())["id"], agent_id)
            previous_stories = dsh_cards.stories(store, self.scope)
            impressions = "## 关于一位还没加入名册的角色\n我对他的第一印象"
            update = dsh_cards.pack("阿墨", "原人格",
                                    {"story.txt": "另一个起点", "impressions.md": impressions},
                                    persona_file="persona.txt")
            response = await self.client.post(route + "?action=update-background&id=" + agent_id,
                                              headers=self.auth, data=update)
            self.assertEqual(response.status, 200)
            downloaded = await self.client.get(route + "?action=download&id=" + agent_id, headers=self.auth)
            self.assertEqual(await downloaded.text(), "原人格")
            self.assertIn(".txt", downloaded.headers["Content-Disposition"])
            downloaded_card = dsh_cards.unpack(dsh_cards.export_card(store, agent_id, "阿墨", "原人格"))
            self.assertEqual(downloaded_card["persona_file"], "persona.txt")
            self.assertEqual(downloaded_card["background"], {"story.txt": "另一个起点",
                "impressions.md": impressions, "stories/灯塔.md": "共见过灯塔"})
            self.assertEqual(dsh_cards.stories(store, self.scope), previous_stories)
            row = DshRoster(store, self.scope).get(agent_id)
            self.assertEqual(row["version"], 1)
            self.assertEqual(row["profiles"][0]["persona"], "原人格")
            self.assertFalse((self.manager.activity / "dsh_team_memory").exists())
            prompt = assignment_prompt(DshRoster(store, self.scope).snapshot(agent_id))
            self.assertIn("[story.txt]\n另一个起点", prompt)
            self.assertIn("[impressions.md]\n" + impressions, prompt)

    async def test_fish_impressions_are_one_plain_document(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        store = ActivityStore(self.manager.activity)
        route = "/api/dsh-team/library"
        document = route + "?document=impressions"
        with closing(store.db):
            response = await self.client.get(document, headers=self.auth)
            self.assertEqual(await response.json(), {"status": "empty"})
            for filename, text in (("他人印象.md", "# 关于小弟甲\r\n旧印象"),
                                   ("他人印象.txt", "关于小弟甲：新印象\r\n关于小弟乙：初见\r\n")):
                saved = await self.client.post(route, headers=self.auth, json={
                    "action": "save-impressions", "id": "fish", "filename": filename, "text": text})
                self.assertEqual(await saved.json(), {"status": "saved"})
                response = await self.client.get(document, headers=self.auth)
                self.assertEqual(await response.json(), {"status": "available", "text": text,
                                                         "filename": "大肥鱼-他人印象.txt"})
            response = await self.client.get(route, headers=self.auth)
            self.assertEqual(await response.json(), {"stories": []})
            self.assertEqual(list(self.manager.activity.glob("fish_impressions.*")),
                             [dsh_cards.fish_impressions_path(store)])

    async def test_role_texts_roundtrip_independently(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        store = ActivityStore(self.manager.activity)
        route = "/api/dsh-team/role-card"
        library = "/api/dsh-team/library"
        with closing(store.db):
            persona = "# 任何标题\r\n\r\n这是原文，不含角色清单。\r\n"
            create = await self.client.post(route + "?action=create&filename=am.txt&name=阿墨",
                                            headers=self.auth, data=persona.encode())
            role = (await create.json())["id"]
            for action, kind, filename, text in (
                    ("save-personal-story", "story", "my-story.md", "# 自选标题\r\n故事正文\r\n"),
                    ("save-impressions", "impressions", "impressions.txt", "关于没入册的角色\r\n")):
                saved = await self.client.post(library, headers=self.auth,
                    json={"action": action, "id": role, "filename": filename, "text": text})
                self.assertEqual(await saved.json(), {"status": "saved"})
                document = await self.client.get(library + f"?document={kind}&id={role}", headers=self.auth)
                self.assertEqual((await document.json())["text"], text)
            revised = persona + "新版本"
            response = await self.client.post(route + f"?action=update-background&filename=am.txt&id={role}",
                                              headers=self.auth, data=revised.encode())
            self.assertEqual((await response.json())["id"], role)
            downloaded = await self.client.get(route + f"?action=download&id={role}", headers=self.auth)
            self.assertEqual(await downloaded.text(), revised)
            roster = DshRoster(store, self.scope)
            self.assertEqual(roster.get(role)["name"], "阿墨")
            self.assertEqual([p["persona"] for p in roster.get(role)["profiles"]], [persona, revised])
            self.assertEqual(set(roster.snapshot(role)["background"]), {"story.md", "impressions.txt"})
            self.assertIsNone(dsh_cards.load_fish_impressions(store))

    async def test_agent_history_archive_carries_selected_history_and_memories_only(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        store = ActivityStore(self.manager.activity)
        with closing(store.db):
            roster = DshRoster(store, self.scope)
            selected = roster.create("阿墨", "原人格")["id"]
            other = roster.create("海玻璃", "另一个人格")["id"]
            dsh_cards.save_background(store, selected, "阿墨", "原人格", {"impressions.txt": "关于海玻璃"})
            long_stories = {f"stories/{name}.md": name * 10_000 for name in ("海上旧站台", "远处的海图")}
            for path, text in long_stories.items():
                dsh_cards.set_story(store, self.scope, filename=path.split("/")[-1], text=text, participants=[selected])
            for agent, ident, note in ((selected, "a" * 16, "自己的对话"), (other, "b" * 16, "无关的对话")):
                store.setting(f"dsh-chat:v1:{self.scope}:{ident}", {"id": ident, "agent_id": agent, "result": note})
            path = self.manager.activity / "dsh_team_memory"
            path.mkdir()
            with closing(sqlite3.connect(path / "livingmemory.db")) as db, db:
                db.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)")
                for agent in (selected, other):
                    session, persona = TeamMemoryBackend.agent_scope(agent)
                    db.execute("INSERT INTO documents(text,metadata) VALUES (?,?)", ("真实经历", json.dumps({
                        "fishhome_owner": agent, "session_id": session, "persona_id": persona,
                        "adapter_id": "fishhome-dsh:" + agent, "fishhome_source_ref": {"ref": "chat:" + "a" * 16}})))
            reply = await self.client.get("/api/dsh-team/role-card?action=history&id=" + selected, headers=self.auth)
            self.assertEqual(reply.status, 200)
            body = await reply.read()
            with ZipFile(BytesIO(body)) as archive:
                self.assertEqual(json.loads(archive.read("identity.json"))["id"], selected)
                self.assertEqual([row["source"]["result"] for row in json.loads(archive.read("history.json"))], ["自己的对话"])
                memory = json.loads(archive.read("memories.json"))
                self.assertEqual([row["metadata"]["fishhome_owner"] for row in memory], [selected])
            preview = await self.client.post("/api/dsh-team/role-card?action=preview-copy", headers=self.auth, data=body)
            self.assertEqual((await preview.json())["original_id"], selected)
            self.assertEqual(len(roster.list()), 2)
            imported = await self.client.post("/api/dsh-team/role-card?action=copy", headers=self.auth, data=body)
            self.assertEqual(imported.status, 200)
            value = await imported.json()
            self.assertNotEqual(value["id"], selected)
            self.assertEqual(value["name"], "阿墨")
            self.assertEqual(roster.get(value["id"])["status"], "paused")
            self.assertEqual(dsh_cards.load_background(store, value["id"]),
                             {"impressions.txt": "关于海玻璃", **long_stories})
            progress = await self.client.get("/api/dsh-team/role-card?action=copy-status&id=" + value["id"],
                                             headers=self.auth)
            self.assertEqual(await progress.json(), {"state": "pending", "copied": 0, "total": 1})
            self.assertEqual(roster.get(selected)["name"], "阿墨")

    async def test_shared_story_is_one_file_and_archive_preserves_mode(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        write_json(self.manager.astrbot_settings, {"agent_runner": {"runner_type": "local",
            "config": {"persona": {"persona_id": "fish-home-v3.0"}}}})
        store = ActivityStore(self.manager.activity)
        with closing(store.db):
            roster = DshRoster(store, self.scope)
            ids = [roster.create("同名角色", "设定")["id"] for _ in range(2)]
            route = "/api/dsh-team/library"
            response = await self.client.post(route, headers=self.auth, json={"action": "save-story",
                "filename": "旧站台.txt", "text": "共同出厂故事", "participants": [*ids, dsh_cards.FISH_PARTICIPANT]})
            self.assertEqual(response.status, 200)
            story_id = (await response.json())["id"]
            source = "共同出厂故事\r\n\r\n" + "长篇正文" * 4000 + "\r\n尾声\r\n"
            changed = await self.client.post(route, headers=self.auth, json={"action": "save-story",
                "id": story_id, "filename": "旧站台.txt", "text": source, "participants": [*ids, dsh_cards.FISH_PARTICIPANT]})
            self.assertEqual(changed.status, 200)
            downloaded = await self.client.get(route + "?id=" + story_id, headers=self.auth)
            self.assertEqual((await downloaded.json())["text"], source)
            pages = []
            offset = 0
            while offset is not None:
                page = dsh_cards.read_story(store, self.scope, story_id, offset)
                pages.append(page["text"])
                offset = page["next_offset"]
            self.assertEqual("".join(pages), source)
            self.assertEqual(len(list((self.manager.activity / "role_stories").glob("*.txt"))), 1)
            exported = [dsh_cards.unpack(dsh_cards.export_card(store, agent, "同名角色", "设定")) for agent in ids]
            self.assertEqual([card["stories"] for card in exported], [{"stories/旧站台.txt": story_id}] * 2)
            self.assertEqual([card["fish_stories"] for card in exported], [["stories/旧站台.txt"]] * 2)
            for action, ident in (("story-status", story_id), ("role-status", ids[0])):
                done = await self.client.post(route, headers=self.auth, json={"action": action, "id": ident, "status": "archived"})
                item = await done.json()
                self.assertEqual((item["status"], item["persona_mode"]), ("archived", "test"))
            self.assertFalse(dsh_cards.load_background(store, ids[1]))
            restored = await self.client.post(route, headers=self.auth,
                json={"action": "story-status", "id": story_id, "status": "active"})
            self.assertEqual(restored.status, 200)
            self.assertEqual(dsh_cards.load_background(store, ids[1]), {"stories/旧站台.txt": source})
            self.assertEqual(roster.get(ids[0])["status"], "archived")
            with tempfile.TemporaryDirectory() as target:
                target_store = ActivityStore(Path(target))
                with closing(target_store.db):
                    elsewhere = DshRoster(target_store, self.scope)
                    for card in exported:
                        copied = elsewhere.create(card["name"], card["persona"])["id"]
                        dsh_cards.import_card_files(target_store, self.scope, copied, card["name"], card)
                    library = dsh_cards.stories(target_store, self.scope)
                    self.assertEqual(len(library), 1)
                    self.assertEqual(len(library[0]["participants"]), 3)
                    self.assertIn(dsh_cards.FISH_PARTICIPANT, library[0]["participants"])
            self.assertFalse((self.manager.activity / "dsh_team_memory").exists())

    async def test_task_and_meeting_console_groups_keep_role_library_shared(self):
        write_json(self.manager.bridge_settings, {"owner_scope": self.scope})
        write_json(self.manager.astrbot_settings, {"agent_runner": {"runner_type": "local",
            "config": {"persona": {"persona_id": "feiyu-v3.0"}}}})
        store = ActivityStore(self.manager.activity)
        with closing(store.db):
            for kind, title in (("commissioned-file", "source"), ("dsh-meeting", "topic")):
                for mode in ("test", "formal"):
                    store.setting(f"{kind}:v1:{self.scope}:{mode}", {"id": mode, title: mode, "created": 1,
                        **({"persona_mode": "formal"} if mode == "formal" else {})})
            for view, expected in (("current", "formal"), ("test", "test")):
                reply = await self.client.get("/api/dsh-team?view=" + view, headers=self.auth)
                data = await reply.json()
                self.assertEqual(data["group"], expected)
                self.assertEqual([row["id"] for row in data["jobs"]], [expected])
                self.assertEqual([row["id"] for row in data["meetings"]], [expected])

    def test_extra_admin_cannot_bypass_private_whitelist(self):
        self.assertEqual(qq_owner(self.config, "12345"), self.scope)
        self.config["platform_settings"]["wl_ignore_admin_on_friend"] = True
        with self.assertRaises(ValueError):
            qq_owner(self.config, "12345")

    async def test_opencode_key_is_saved_encrypted_and_only_reports_presence(self):
        value = "oc-test-placeholder-for-storage"
        denied = await self.client.post("/api/config", headers={"Host": self.auth["Host"]}, json={"opencode_key": value})
        self.assertEqual(denied.status, 401)
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertFalse((await result.json())["opencode_key_saved"])
        saved = await self.client.post("/api/config", headers=self.auth, json={"opencode_key": value})
        self.assertEqual(saved.status, 200)
        self.assertNotIn(value, await saved.text())
        credentials = Credentials(self.manager.creds.path)
        self.assertEqual(credentials.values["opencode_key"], value)
        self.assertNotIn(value.encode(), credentials.path.read_bytes())
        self.assertNotIn("upstream_key", credentials.values)
        for url in ("/api/config", "/api/provider-keys"):
            async with self.client.get(url, headers=self.auth) as result:
                payload = await result.json()
                self.assertNotIn(value, str(payload))
                if url == "/api/config":
                    self.assertTrue(payload["opencode_key_saved"])

    async def test_desktop_password_is_encrypted_and_never_returned(self):
        value = "desktop-password-fixture"
        denied = await self.client.post("/api/config", headers={"Host": self.auth["Host"]},
                                        json={"desktop_password": value})
        self.assertEqual(denied.status, 401)
        with patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()) as logon:
            saved = await self.client.post("/api/config", headers=self.auth, json={"desktop_password": value})
        self.assertEqual(saved.status, 200)
        self.assertNotIn(value, await saved.text())
        logon.assert_called_once_with("FishHomeDesktop", value)
        credentials = Credentials(self.manager.creds.path)
        self.assertEqual(credentials.values["desktop_password"], value)
        self.assertNotIn(value.encode(), credentials.path.read_bytes())
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)):
            async with self.client.get("/api/config", headers=self.auth) as response:
                payload = await response.json()
        self.assertTrue(payload["desktop_password_saved"])
        self.assertNotIn(value, str(payload))

    async def test_desktop_auto_requires_password_and_controls_all_start(self):
        self.assertNotIn("desktop", self.manager.specs())
        response = await self.client.post("/api/config", headers=self.auth, json={"desktop_auto_enabled": True})
        self.assertEqual(response.status, 409)
        self.assertFalse(self.manager.options.get("desktop_auto_enabled", False))
        self.manager.creds.values["desktop_password"] = "fixture"
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name) / "profile"):
            service = self.manager.specs()["desktop"]
            self.assertEqual(service.account, "FishHomeDesktop")
            with patch.object(self.manager, "action", new_callable=AsyncMock, return_value="started") as action:
                response = await self.client.post("/api/services/all/start", headers=self.auth)
                self.assertEqual(response.status, 200)
                self.assertNotIn("desktop", (await response.json())["results"])
                action.assert_any_await("budget", "start")
                action.reset_mock()
                response = await self.client.post("/api/config", headers=self.auth, json={"desktop_auto_enabled": True})
                self.assertEqual(response.status, 200)
                response = await self.client.post("/api/services/all/start", headers=self.auth)
                self.assertEqual((await response.json())["results"]["desktop"], "started")
                action.assert_any_await("desktop", "start")

    async def test_desktop_running_in_another_session_is_not_reported_as_running(self):
        self.manager.creds.values["desktop_password"] = "fixture"
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=True), \
             patch("deploy.desktop_manager.desktop_session.get_session", return_value={"session_id": 1}), \
             patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()) as impersonate, \
             patch("deploy.desktop_manager.desktop_session.session_id", return_value=2) as session_id:
            with self.assertRaisesRegex(ValueError, "另一 Windows 会话"):
                await self.manager.start("desktop")
            session_id.assert_called_once_with(123)
            impersonate.assert_called_once_with("FishHomeDesktop", "fixture")
            session_id.return_value = 1
            self.assertEqual(await self.manager.start("desktop"), "已在运行")

    async def test_desktop_auto_start_is_locked_and_failure_does_not_break_console(self):
        self.manager.creds.values["desktop_password"] = "fixture"
        self.manager.options["desktop_auto_enabled"] = True
        async def fail_start(name):
            self.assertEqual(name, "desktop")
            self.assertTrue(self.manager.lock.locked())
            raise RuntimeError("private error")
        with patch.object(self.manager, "start", side_effect=fail_start) as start:
            client = TestClient(TestServer(create_app(self.manager, "other-token")))
            await client.start_server()
            try:
                start.assert_awaited_once_with("desktop")
                response = await client.get("/api/ping", headers={"Host": "127.0.0.1:6196", "Authorization": "Bearer other-token"})
                self.assertEqual(response.status, 200)
            finally:
                await client.close()
        log = (self.manager.state / "manager.log").read_text(encoding="utf-8")
        self.assertIn("桌面服务自动启动失败：RuntimeError", log)
        self.assertNotIn("private error", log)

    async def test_desktop_auto_start_requires_both_settings(self):
        self.manager.options["desktop_auto_enabled"] = True
        with patch.object(self.manager, "start", new_callable=AsyncMock) as start:
            client = TestClient(TestServer(create_app(self.manager, "other-token")))
            await client.start_server()
            await client.close()
            start.assert_not_awaited()

    async def test_desktop_stop_refusal_preserves_tracking(self):
        self.manager.creds.values.update(desktop_password="fixture", desktop_token="token-fixture")
        self.manager.launches["desktop"] = {"pid": 123, "time": 456}
        write_json(self.manager.launch_file, self.manager.launches)
        response = MagicMock(status=409)
        response.json = AsyncMock(return_value={"error": "任务正在运行"})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.post.return_value = request
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=session)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=123), \
             patch("deploy.desktop_manager.manager.impersonate", return_value=nullcontext()), \
             patch("deploy.desktop_manager.manager.psutil.Process") as process, \
             patch("deploy.desktop_manager.manager.ClientSession", return_value=client):
            process.return_value.create_time.return_value = 456
            with self.assertRaisesRegex(ValueError, "任务正在运行"):
                await self.manager.stop("desktop")
        session.post.assert_called_once_with("http://127.0.0.1:59937/desktop", json={"op": "shutdown"},
                                             headers={"Authorization": "Bearer token-fixture"})
        self.assertEqual(self.manager.launches["desktop"], {"pid": 123, "time": 456})
        self.assertEqual(read_settings(self.manager.launch_file)["desktop"], {"pid": 123, "time": 456})

    async def test_desktop_shutdown_waits_for_exit_and_retains_tracking_on_timeout(self):
        self.manager.creds.values.update(desktop_password="fixture", desktop_token="token-fixture")
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"ok": True})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.post.return_value = request
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=session)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch("deploy.desktop_manager.manager.compute.profile", return_value=Path(self.temp.name)), \
             patch.object(self.manager, "listeners", return_value={59937: {123}}), \
             patch.object(self.manager, "matching_pid", return_value=123), \
             patch("deploy.desktop_manager.manager.impersonate", side_effect=lambda *args: nullcontext()) as login, \
             patch("deploy.desktop_manager.manager.psutil.Process") as process, \
             patch("deploy.desktop_manager.manager.ClientSession", return_value=client):
            process.return_value.create_time.return_value = 456
            self.manager.launches["desktop"] = {"pid": 123, "time": 456}
            write_json(self.manager.launch_file, self.manager.launches)
            process.return_value.wait.side_effect = psutil.TimeoutExpired(8, 123)
            with self.assertRaisesRegex(ValueError, "尚未退出"):
                await self.manager.stop("desktop")
            self.assertIn("desktop", self.manager.launches)
            self.assertIn("desktop", read_settings(self.manager.launch_file))
            process.return_value.wait.assert_called_with(timeout=8)
            self.assertGreaterEqual(login.call_count, 2)
            process.return_value.wait.side_effect = None
            result = await self.manager.stop("desktop")
            self.assertEqual(result, "桌面服务已停止")
            self.assertNotIn("desktop", self.manager.launches)
            self.assertNotIn("desktop", read_settings(self.manager.launch_file))

    async def test_public_video_has_separate_credentials_and_switch(self):
        path = "/api/worker/public-video"
        worker = AsyncMock(return_value={"kind": "dsh_video", "summary": "sample"})
        self.manager.video_worker.run = worker
        self.manager.video_worker.dsh.mkdir(parents=True)
        (self.manager.video_worker.dsh / "Start-DSH-Video.cmd").touch()
        self.manager.creds.values.update(opencode_key="go-key-fixture", runner_password="runner-fixture")
        body = {"reference": "BV1g8Wq6DEXj", "question": "核对开头"}
        self.assertEqual((await self.client.post(path, headers=self.auth, json=body)).status, 401)
        self.assertEqual((await self.client.post("/api/config", headers=self.auth, json={"dsh_video_enabled": True})).status, 200)
        token = read_settings(self.manager.bridge_settings)["video_worker_token"]
        headers = {**self.auth, "Authorization": "Bearer " + token}
        self.assertEqual((await self.client.post(path, headers=self.auth, json=body)).status, 401)
        self.assertEqual((await self.client.get("/api/config", headers=headers)).status, 401)
        result = await self.client.post(path, headers=headers, json=body)
        self.assertEqual(await result.json(), {"kind": "dsh_video", "summary": "sample"})
        worker.assert_awaited_once_with("runner-fixture", "go-key-fixture", *body.values())
        await self.client.post("/api/config", headers=self.auth, json={"dsh_video_enabled": False})
        self.assertEqual((await self.client.post(path, headers=headers, json=body)).status, 401)

    async def test_activity_settings_read_defaults_and_change_the_runtime_file(self):
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual((await result.json())["activity_settings"]["contact_daily_limit"], 3)
        for invalid in (True, "5", 51):
            reply = await self.client.post("/api/config", headers=self.auth, json={"contact_daily_limit": invalid})
            self.assertEqual(reply.status, 409)
        reply = await self.client.post("/api/config", headers=self.auth, json={"contact_daily_limit": 5})
        self.assertEqual(reply.status, 200)
        self.assertEqual(read_settings(self.manager.control), {"public_interactions": False, "contact_daily_limit": 5})
        self.assertTrue(list((self.manager.activity / "config_backups").glob("control-*.json")))
        async with self.client.get("/api/config", headers=self.auth) as result:
            self.assertEqual((await result.json())["activity_settings"]["contact_daily_limit"], 5)

    async def test_activity_toggle_preserves_prerequisites_and_source_edits_preserve_credentials(self):
        response = await self.client.post("/api/config", headers=self.auth, json={"periodic_enabled": True})
        self.assertEqual(response.status, 409)
        response = await self.client.post("/api/config", headers=self.auth, json={"commission_sources": ["../secret.txt"]})
        self.assertEqual(response.status, 409)
        response = await self.client.post("/api/config", headers=self.auth, json={"commission_sources": ["sample.csv"]})
        self.assertEqual(response.status, 200)
        async with self.client.get("/api/config", headers=self.auth) as result:
            payload = await result.json()
            self.assertEqual(payload["dsh_sources"]["commission_sources"], ["sample.csv"])
            self.assertNotIn("private-cookie", str(payload))
        self.assertIn("private-cookie", self.manager.bridge_settings.read_text(encoding="utf-8"))

    async def test_budget_update_uses_existing_shared_ledger(self):
        ledger = Ledger(self.manager.ledger)
        budget = {"bucket": "autonomous", "amount": "0", "temporary": True}
        async with self.client.post("/api/budgets", headers=self.auth, json=budget) as result:
            self.assertEqual(result.status, 200)
        async with self.client.get("/api/budgets", headers=self.auth) as result:
            self.assertEqual(result.status, 200)
            self.assertEqual(await result.json(), ledger.snapshot())
            self.assertEqual(ledger.snapshot()["ledgers"]["autonomous"]["limit"], "0")
            self.assertEqual(ledger.snapshot()["ledgers"]["interaction"]["limit"], "5")

    async def test_shared_settings_are_authenticated_and_reject_stale_or_out_of_scope_updates(self):
        Ledger(self.manager.ledger)
        denied = await self.client.get("/api/shared-settings", headers={"Host": self.auth["Host"]})
        self.assertEqual(denied.status, 401)
        async with self.client.get("/api/shared-settings", headers=self.auth) as response:
            defaults = await response.json()
            self.assertEqual(defaults["default_interaction_budget"], "5")
            self.assertNotIn("periodic_enabled", defaults)
            self.assertNotIn("opencode_key", defaults)
        bad = {"expected": {"periodic_enabled": False}, "changes": {"periodic_enabled": True}}
        response = await self.client.post("/api/shared-settings", headers=self.auth, json=bad)
        self.assertEqual(response.status, 409)
        stale = {"expected": {"contact_daily_limit": 99}, "changes": {"contact_daily_limit": 5}}
        response = await self.client.post("/api/shared-settings", headers=self.auth, json=stale)
        self.assertEqual(response.status, 409)
        stale["expected"]["contact_daily_limit"] = 4
        async with self.client.post("/api/shared-settings", headers=self.auth, json=stale) as response:
            self.assertEqual((await response.json())["contact_daily_limit"], 3)
        stale["expected"]["contact_daily_limit"] = 3
        async with self.client.post("/api/shared-settings", headers=self.auth, json=stale) as response:
            self.assertEqual((await response.json())["contact_daily_limit"], 5)

    async def test_reject_public_enable_with_old_activity_plugin(self):
        async with self.client.post("/api/config", headers=self.auth, json={"public_write": True}) as result:
            self.assertEqual(result.status, 409)
        self.assertFalse(self.manager.configuration()["public_write"])

    async def test_backup_route_creates_private_safe_archive_and_lists_it(self):
        memory = self.manager.astro / LM / "livingmemory.db"
        memory.parent.mkdir(parents=True)
        with closing(sqlite3.connect(memory)) as db, db:
            db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
        Ledger(self.manager.ledger)
        async with self.client.get("/api/archives", headers={"Host": "127.0.0.1:6196"}) as result:
            self.assertEqual(result.status, 401)
        with patch("deploy.desktop_manager.manager.ROOT", self.manager.dev.parent):
            async with self.client.post("/api/archives/backup", headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                name = (await result.json())["name"]
        manifest = verify(self.manager.backups / name)
        self.assertIsNone(manifest["revision"])
        self.assertEqual(manifest["environment"], "prod")
        self.assertEqual(manifest["consistency"], "sqlite-online")
        self.assertFalse(manifest["include_private_chat"])
        async with self.client.get("/api/archives", headers=self.auth) as result:
            self.assertEqual([row["name"] for row in (await result.json())["backups"]], [name])

    async def test_recovery_route_is_desktop_only_and_lists_package(self):
        from deploy.desktop_manager import manager as console

        route = "/api/archives/recovery"
        async with self.client.post(route, headers=self.auth) as result:
            self.assertEqual(result.status, 409)
        name = "thinkbook-recovery-20261113T092729.770820Z.zip"

        def pull(destination):
            destination.mkdir()
            package = destination / name
            package.touch()
            return package

        with patch.object(console, "DESKTOP_HOME", self.manager.dev.parent), patch.object(console, "capture_and_pull", pull):
            async with self.client.get("/", headers=self.auth) as result:
                self.assertIn('id="recovery">', await result.text())
            async with self.client.post(route, headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                self.assertEqual((await result.json())["name"], name)
        async with self.client.get("/api/archives", headers=self.auth) as result:
            self.assertEqual([row["name"] for row in (await result.json())["backups"]], [name])

    async def test_thinkbook_export_uses_desktop_exports_directory(self):
        from deploy.desktop_manager import manager as console

        route = "/api/archives/export-thinkbook"
        async with self.client.post(route, headers=self.auth) as result:
            self.assertEqual(result.status, 409)
        with (patch.object(console, "DESKTOP_HOME", self.manager.dev.parent),
              patch.object(console, "export_from_thinkbook", side_effect=lambda path: path) as export):
            async with self.client.get("/", headers=self.auth) as result:
                self.assertIn('id="export-thinkbook">', await result.text())
            async with self.client.post(route, headers=self.auth) as result:
                self.assertEqual(result.status, 200)
                self.assertEqual((await result.json())["name"], export.call_args.args[0].name)
        self.assertEqual(export.call_args.args[0].parent, self.manager.exports)
