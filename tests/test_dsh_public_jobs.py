import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager, closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from deploy.dsh_bridge.go_relay import GoSubscriptionWaiting

if os.name == "nt":
    from deploy.desktop_manager.manager import create_app
    from deploy.desktop_manager.public_jobs import PublicJobs
    from deploy.desktop_manager import video_worker
    from deploy.desktop_manager.video_worker import VideoWorker
    from deploy.desktop_manager.windows import write_json


VIDEO = {"kind": "video", "reference": "BV1g8Wq6DEXj", "question": "Describe the opening."}
SEARCH = {"kind": "web", "query": "Python pathlib documentation"}
VIDEO_ID = "a" * 32
SEARCH_ID = "b" * 32
EXTRA_ID = "c" * 32
WORKER_AUTH = {"Host": "127.0.0.1:6196", "Authorization": "Bearer worker-token"}


async def eventually(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


def result(body):
    return {"kind": "dsh_video" if body["kind"] == "video" else "dsh_web_search",
            "summary": "evidence summary", "evidence": [{"title": "evidence"}]}


@unittest.skipUnless(os.name == "nt", "Windows desktop manager")
class PublicJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    async def test_unstarted_job_reopens_only_after_subscription_available(self):
        calls = []
        directory = self.root / "jobs"

        async def execute(body, started):
            started()
            calls.append(body)
            if len(calls) == 1:
                raise GoSubscriptionWaiting("Go rejected before first receipt")
            return result(body)

        job = PublicJobs(directory, execute)
        job.submit(SEARCH_ID, SEARCH)
        job.start()
        await eventually(lambda: job.get(SEARCH_ID)["state"] == "go_wait")
        await job.close()
        self.assertEqual(calls, [SEARCH])

        reopened = PublicJobs(directory, execute, quota_available=AsyncMock(return_value=True))
        release = asyncio.Event()
        with patch.object(reopened, "_await_quota", new=release.wait):
            reopened.start()
            await asyncio.sleep(0)
            self.assertEqual(reopened.get(SEARCH_ID)["state"], "go_wait")
            self.assertEqual(calls, [SEARCH])
            release.set()
            await eventually(lambda: reopened.get(SEARCH_ID)["state"] == "completed")
        await reopened.close()
        self.assertEqual(calls, [SEARCH, SEARCH])

    async def test_personal_page_uses_same_archive_without_go_worker(self):
        page = {"kind": "page_text", "title": "Technical docs", "url": "https://example.org/docs",
                "requested_url": "https://example.org/docs", "fetched_at": "2026-11-18T00:00:00Z",
                "text": "正文" * 5000}
        search = AsyncMock()
        jobs = PublicJobs(self.root / "jobs", AsyncMock())
        manager = SimpleNamespace(options={"dsh_web_search_enabled": True},
            creds=SimpleNamespace(values={"video_worker_token": "worker-token"}),
            sync_video_worker=lambda: "worker-token", public_jobs=jobs, room_snapshot=None,
            astro=self.root / "missing", settings_sync=None, video_worker=SimpleNamespace(search=search),
            archive_web_page=lambda _: "shared/web-sources/example.md")
        with patch("deploy.desktop_manager.manager.fetch_public_page", AsyncMock(return_value=page)) as read:
            async with TestClient(TestServer(create_app(manager, "console-token"))) as client:
                response = await client.post("/api/worker/public-search", headers=WORKER_AUTH,
                                             json={"mode": "page", "query": page["url"]})
                self.assertEqual(response.status, 200)
                item = await response.json()
        read.assert_awaited_once_with(page["url"])
        search.assert_not_awaited()
        self.assertEqual(item["artifact"], "shared/web-sources/example.md")
        self.assertEqual((item["next_offset"], item["total_chars"]), (6000, 10000))

    async def test_worker_api_accepts_parallel_jobs_and_reads_cached_results(self):
        release = asyncio.Event()
        calls = []

        async def execute(body, started):
            started()
            calls.append(body)
            await release.wait()
            return result(body)

        enabled = {"video", "web"}
        jobs = PublicJobs(self.root / "jobs", execute)
        manager = SimpleNamespace(options={},
            creds=SimpleNamespace(values={"video_worker_token": "worker-token"}),
            public_job_enabled=lambda kind: kind in enabled,
            sync_video_worker=lambda: "worker-token",
            public_jobs=jobs, room_snapshot=None, astro=self.root / "missing",
            settings_sync=None,
        )
        video_url = f"/api/worker/public-jobs/{VIDEO_ID}"
        search_url = f"/api/worker/public-jobs/{SEARCH_ID}"
        extra_url = f"/api/worker/public-jobs/{EXTRA_ID}"
        named_search = {**SEARCH, "agent": {
            "id": "d" * 16, "name": "查证鱼", "version": 2, "persona": "晚班资料员" * 2000,
            "guides": [{"id": "e" * 16, "title": "来源核对", "version": 1, "text": "核对官方出处"}],
        }}
        async with TestClient(TestServer(create_app(manager, "console-token"))) as client:
            denied = await client.get(video_url, headers={
                "Host": "127.0.0.1:6196", "Authorization": "Bearer console-token"})
            self.assertEqual(denied.status, 401)
            invalid = {**named_search, "agent": {**named_search["agent"], "version": True}}
            self.assertEqual((await client.put(extra_url, headers=WORKER_AUTH, json=invalid)).status, 409)
            for url, body in ((video_url, VIDEO), (search_url, named_search)):
                accepted = await client.put(url, headers=WORKER_AUTH, json=body)
                self.assertEqual(accepted.status, 200)
                self.assertEqual((await accepted.json())["state"], "queued")
            await eventually(lambda: len(calls) == 2)
            self.assertEqual(jobs.get(VIDEO_ID)["state"], "running")
            self.assertEqual(jobs.get(SEARCH_ID)["state"], "running")
            enabled.clear()
            duplicate = await client.put(search_url, headers=WORKER_AUTH, json=named_search)
            self.assertEqual((await duplicate.json())["state"], "running")
            different = await client.put(search_url, headers=WORKER_AUTH, json={**SEARCH, "query": "other"})
            self.assertEqual(different.status, 409)
            blocked = await client.put(extra_url, headers=WORKER_AUTH, json=SEARCH)
            self.assertEqual(blocked.status, 409)
            self.assertEqual((await (await client.get(extra_url, headers=WORKER_AUTH)).json())["state"], "not_found")
            cancelling = await client.delete(video_url, headers=WORKER_AUTH)
            self.assertEqual((await cancelling.json())["state"], "cancelling")
            await eventually(lambda: jobs.get(VIDEO_ID)["state"] == "cancelled")
            release.set()
            await eventually(lambda: jobs.get(SEARCH_ID)["state"] == "completed")
            search = await (await client.get(search_url, headers=WORKER_AUTH)).json()
            self.assertEqual(search["report"], result(SEARCH))
            self.assertNotIn("body", search)
            self.assertEqual(calls, [VIDEO, named_search])
            self.assertNotIn(named_search["agent"]["persona"], json.dumps(search, ensure_ascii=False))

    async def test_restart_resumes_queued_and_keeps_interrupted_unknown(self):
        directory = self.root / "jobs"
        old = PublicJobs(directory, None)
        old.submit(VIDEO_ID, VIDEO)
        old.submit(SEARCH_ID, SEARCH)
        write_json(directory / (VIDEO_ID + ".json"),
                   {"id": VIDEO_ID, "body": VIDEO, "state": "running", "created": 1})
        calls = []

        async def execute(body, started):
            started()
            calls.append(body)
            return result(body)

        reopened = PublicJobs(directory, execute)
        reopened.start()
        self.addAsyncCleanup(reopened.close)
        review = reopened.get(VIDEO_ID)
        self.assertEqual(review["state"], "needs_review")
        self.assertEqual(review["failure"]["code"], "worker_interrupted")
        await eventually(lambda: reopened.get(SEARCH_ID)["state"] == "completed")
        self.assertEqual(calls, [SEARCH])

    async def test_unknown_cancel_blocks_delayed_submit_and_errors_keep_private_details(self):
        calls = []

        async def execute(body, started):
            calls.append(body)
            started()
            raise RuntimeError("secret-credential")

        original = PublicJobs(self.root / "jobs", execute)
        self.assertEqual(original.cancel(VIDEO_ID)["state"], "cancelled")
        jobs = PublicJobs(self.root / "jobs", execute)
        jobs.start()
        self.addAsyncCleanup(jobs.close)
        self.assertEqual(jobs.submit(VIDEO_ID, VIDEO)["state"], "cancelled")
        jobs.submit(SEARCH_ID, SEARCH)
        await eventually(lambda: jobs.get(SEARCH_ID)["state"] == "failed")
        failed = jobs.get(SEARCH_ID)
        self.assertEqual(failed["failure"]["code"], "worker_failed")
        self.assertNotIn("secret-credential", str(failed))
        self.assertEqual(calls, [SEARCH])

    async def test_long_search_task_retains_phase_after_timeout(self):
        query = "查证公开资料" * 100

        async def execute(body, started):
            self.assertEqual(body["query"], query)
            started("waiting_for_result")
            raise TimeoutError()

        jobs = PublicJobs(self.root / "jobs", execute)
        jobs.start()
        self.addAsyncCleanup(jobs.close)
        jobs.submit(SEARCH_ID, {"kind": "web", "query": query})
        await eventually(lambda: jobs.get(SEARCH_ID)["state"] == "failed")
        reopened = PublicJobs(self.root / "jobs", execute)
        self.assertEqual(reopened.get(SEARCH_ID)["phase"], "waiting_for_result")
        self.assertEqual(reopened.get(SEARCH_ID)["failure"]["code"], "timed_out")

    async def test_manager_close_marks_running_job_unconfirmed(self):
        async def execute(body, started):
            started()
            await asyncio.Event().wait()

        jobs = PublicJobs(self.root / "jobs", execute)
        jobs.start()
        jobs.submit(VIDEO_ID, VIDEO)
        jobs.submit(SEARCH_ID, SEARCH)
        await eventually(lambda: all(jobs.get(ident)["state"] == "running" for ident in (VIDEO_ID, SEARCH_ID)))
        await jobs.close()
        self.assertEqual(jobs.get(VIDEO_ID)["state"], "needs_review")
        self.assertEqual(jobs.get(SEARCH_ID)["state"], "needs_review")

    async def test_console_cancel_only_selects_the_owner_public_job(self):
        scope = "qq:FriendMessage:123"
        ledger = self.root / "activity.sqlite3"
        bridge = self.root / "bridge.json"
        write_json(bridge, {"owner_scope": scope})
        short = "7" * 12
        outside = "8" * 12
        file_id = "9" * 12
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
            for event_scope, ident, kind, worker_id in (
                (scope, short, "video", VIDEO_ID),
                ("qq:FriendMessage:999", outside, "video", SEARCH_ID),
                (scope, file_id, "file", EXTRA_ID),
            ):
                db.execute("INSERT INTO settings VALUES(?,?)", (
                    f"commissioned-file:v1:{event_scope}:{ident}",
                    json.dumps({"job_kind": kind, "state": "prepared", "task_id": worker_id})))
        jobs = PublicJobs(self.root / "jobs", AsyncMock())
        manager = SimpleNamespace(options={},
            creds=SimpleNamespace(values={"video_worker_token": "worker-token"}),
            public_job_enabled=lambda _: True, sync_video_worker=lambda: "worker-token",
            public_jobs=jobs, room_snapshot=None, astro=self.root / "missing", settings_sync=None,
            bridge_settings=bridge, ledger=ledger, astrbot_settings=self.root / "cmd_config.json",
        )
        headers = {"Host": "127.0.0.1:6196", "Authorization": "Bearer console-token"}
        async with TestClient(TestServer(create_app(manager, "console-token"))) as client:
            path = "/api/dsh-team/cancel"
            for ident in (outside, file_id):
                denied = await client.post(path, headers=headers, json={"id": ident})
                self.assertEqual(denied.status, 409)
            response = await client.post(path, headers=headers, json={"id": short})
            self.assertEqual(await response.json(), {"id": short, "state": "cancelled"})
            self.assertEqual(jobs.submit(VIDEO_ID, VIDEO)["state"], "cancelled")
        self.assertEqual(jobs.get(SEARCH_ID)["state"], "not_found")
        self.assertEqual(jobs.get(EXTRA_ID)["state"], "not_found")
        jobs.execute.assert_not_awaited()

    async def test_more_than_two_web_tasks_launch_even_while_the_desktop_is_busy(self):
        worker = VideoWorker(self.root, self.root, "runner")
        release = asyncio.Event()
        launched = set()

        async def run(_password, _key, _server, _patch, _execute, _upstream, *, progress, startup_timeout):
            self.assertEqual(startup_timeout, 90)
            launched.add(asyncio.current_task())
            await release.wait()
            return result(SEARCH)

        worker._run = run
        jobs = PublicJobs(self.root / "jobs", lambda body, started:
            worker.search("unused", "unused", body["query"], search=AsyncMock(), background_started=started))
        jobs.start()
        self.addAsyncCleanup(jobs.close)
        async with worker.desktop_lock:
            for ident in (VIDEO_ID, SEARCH_ID, EXTRA_ID):
                jobs.submit(ident, SEARCH)
            await eventually(lambda: len(launched) == 3)
            release.set()
        await eventually(lambda: all(jobs.get(ident)["state"] == "completed"
                                     for ident in (VIDEO_ID, SEARCH_ID, EXTRA_ID)))

    async def test_worker_restarts_before_session_creation_and_executes_only_once(self):
        (self.root / "Start-DSH-Video.cmd").touch()
        (self.root / "AppData/Local/FishHome").mkdir(parents=True)
        worker = VideoWorker(self.root, self.root, "runner")
        connected = object()
        connect = AsyncMock(side_effect=[video_worker.WorkerStartupError("early exit"), connected])
        execute = AsyncMock(return_value=result(SEARCH))
        phases = []

        @asynccontextmanager
        async def listener(_server, _token):
            yield "http://127.0.0.1:1/mcp"

        with patch.object(video_worker, "media_listener", listener), \
                patch.object(video_worker, "impersonate", return_value=nullcontext("runner-token")), \
                patch.object(video_worker.win32profile, "GetUserProfileDirectory", return_value=str(self.root)), \
                patch.object(video_worker, "run_as", side_effect=[101, 102]) as launch, \
                patch.object(video_worker, "connect_worker", connect), \
                patch.object(video_worker, "stop_worker", AsyncMock()) as stop, \
                patch.object(video_worker, "wait_for_memory", AsyncMock()):
            report = await worker._run("unused", "unused", None, lambda *_: [], execute, None,
                                       progress=phases.append)
        self.assertEqual(report, result(SEARCH))
        self.assertEqual(launch.call_count, 2)
        self.assertEqual([call.args[-1] for call in stop.await_args_list], [101, 102])
        self.assertEqual(execute.await_count, 1)
        self.assertIs(execute.await_args.args[0], connected)
        self.assertIn("restarting_worker", phases)
