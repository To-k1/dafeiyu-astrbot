import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

if os.name == "nt":
    from deploy.desktop_manager.manager import create_app
    from deploy.desktop_manager.public_jobs import PublicJobs
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
        manager = SimpleNamespace(
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

    async def test_manager_close_marks_running_job_unconfirmed(self):
        async def execute(body, started):
            started()
            await asyncio.Event().wait()

        jobs = PublicJobs(self.root / "jobs", execute, parallel=1)
        jobs.start()
        jobs.submit(VIDEO_ID, VIDEO)
        jobs.submit(SEARCH_ID, SEARCH)
        await eventually(lambda: jobs.get(VIDEO_ID)["state"] == "running")
        await jobs.close()
        self.assertEqual(jobs.get(VIDEO_ID)["state"], "needs_review")
        self.assertEqual(jobs.get(SEARCH_ID)["state"], "queued")

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
        manager = SimpleNamespace(
            creds=SimpleNamespace(values={"video_worker_token": "worker-token"}),
            public_job_enabled=lambda _: True, sync_video_worker=lambda: "worker-token",
            public_jobs=jobs, room_snapshot=None, astro=self.root / "missing", settings_sync=None,
            bridge_settings=bridge, ledger=ledger,
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

    async def test_background_launch_waits_for_shared_slot(self):
        worker = VideoWorker(self.root, self.root, "runner", parallel=1)
        worker._run = AsyncMock(return_value=result(SEARCH))
        started = asyncio.Event()
        async with worker.slots:
            task = asyncio.create_task(worker.search("unused", "unused", SEARCH["query"],
                                                     background_started=started.set))
            await asyncio.sleep(0)
            self.assertFalse(started.is_set())
            with self.assertRaisesRegex(ValueError, "均在使用"):
                await worker.search("unused", "unused", SEARCH["query"])
        self.assertEqual(await task, result(SEARCH))
        self.assertTrue(started.is_set())
        worker._run.assert_awaited_once()
