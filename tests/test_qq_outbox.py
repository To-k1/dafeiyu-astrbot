import asyncio
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from deploy.patches.qq_outbox import DeliveryFailed, DeliveryUncertain, QQOutbox, QueuedDelivery
from deploy.patches.qq_outbox_adapter import install_outbox
from aiocqhttp.api import AsyncApi


class MissingConnection(Exception):
    pass


class ExplicitRefusal(Exception):
    retcode = 1200


class OutboxAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_onebot_dynamic_send_stages_until_account_is_online(self):
        class OneBot(AsyncApi):
            def __init__(self):
                self._wsr_api_clients = {"123": object()}
                self.online = False
                self.sent = []

            async def call_action(self, action, **params):
                if action == "get_status":
                    return {"online": self.online}
                self.sent.append((action, params))
                return {"message_id": len(self.sent)}

        with tempfile.TemporaryDirectory() as folder:
            bot = OneBot()
            outbox = install_outbox(bot, Path(folder) / "outbox.sqlite3", lambda: "456")
            outbox.first_delay = outbox.following_delay = (0, 0)
            outbox.poll_interval = .01
            try:
                queued = await bot.send_private_msg(user_id=456, message="ordinary reply")
                later = await bot.fishhome_submit("send_private_msg", {"user_id": 456, "message": "reminder"},
                                                  key="reminder-1")
                await asyncio.sleep(.03)
                self.assertEqual(bot.sent, [])
                self.assertEqual(outbox.summary(), {"pending": 2})
                with self.assertRaises(PermissionError):
                    await bot.send_private_msg(user_id=789, message="wrong recipient")
                bot.online = True
                bot._wsr_api_clients["123"] = object()
                await outbox.wait(later["fishhome_queue_id"], timeout=1)
                self.assertEqual([params["message"] for _, params in bot.sent], ["ordinary reply", "reminder"])
                self.assertEqual(outbox.status(queued["fishhome_queue_id"])["message_id"], "1")
            finally:
                await outbox.close()


class QQOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "outbox.sqlite3"
        self.online = False
        self.allowed = True
        self.calls = []
        self.outcomes = []

    def outbox(self, **options):
        async def ready(action, params):
            return self.online

        async def send(action, **params):
            self.calls.append((action, params))
            result = self.outcomes.pop(0) if self.outcomes else {"message_id": len(self.calls)}
            if isinstance(result, Exception):
                raise result
            return result(params) if callable(result) else result

        queue = QQOutbox(self.path, send=send, ready=ready, allowed=lambda *args: self.allowed,
                         unavailable_errors=(MissingConnection,), rejected_errors=(ExplicitRefusal,),
                         first_delay=(0, 0), following_delay=(0, 0), poll_interval=.01, **options)
        self.addAsyncCleanup(queue.close)
        return queue

    async def enqueue(self, queue, content, *, key=None):
        with self.assertRaises(QueuedDelivery) as pending:
            await queue.submit("send_private_msg", {"user_id": 123, "message": content}, key=key, wait=0)
        return pending.exception.entry_id

    async def test_offline_restart_replays_fifo_and_returns_existing_receipt(self):
        queue = self.outbox()
        first = await self.enqueue(queue, "first", key="test-first")
        second = await self.enqueue(queue, [{"type": "record", "data": {"file": "base64://YQ=="}}])
        await asyncio.sleep(.03)
        self.assertEqual(self.calls, [])
        await queue.close()
        self.online = True
        queue = self.outbox()
        queue.start()
        result = await queue.submit("send_private_msg", {"user_id": 123, "message": "first"}, key="test-first")
        self.assertEqual(result, {"message_id": 1})
        await queue.wait(second, timeout=1)
        self.assertEqual(queue.status(first)["state"], "sent")
        self.assertEqual(queue.status(second)["message_id"], "2")
        self.assertEqual([call[1]["message"] for call in self.calls], [
            "first", [{"type": "record", "data": {"file": "base64://YQ=="}}],
        ])

    async def test_uncertain_delivery_does_not_resend_or_block_the_next_item(self):
        queue = self.outbox()
        first = await self.enqueue(queue, "maybe delivered")
        second = await self.enqueue(queue, "next")
        self.outcomes = [TimeoutError(), {"message_id": 12}]
        self.online = True
        with self.assertRaises(DeliveryUncertain):
            await queue.wait(first, timeout=1)
        self.assertEqual(await queue.wait(second, timeout=1), {"message_id": 12})
        await queue.close()
        queue = self.outbox()
        queue.start()
        await asyncio.sleep(.04)
        self.assertEqual(queue.summary(), {"uncertain": 1, "sent": 1})
        self.assertEqual(len(self.calls), 2)

    async def test_presend_disconnect_retries_and_explicit_rejection_is_terminal(self):
        queue = self.outbox()
        first = await self.enqueue(queue, "safe retry")
        second = await self.enqueue(queue, "refused")
        self.outcomes = [MissingConnection(), {"message_id": 42}, ExplicitRefusal()]
        self.online = True
        self.assertEqual(await queue.wait(first, timeout=1), {"message_id": 42})
        with self.assertRaises(DeliveryFailed):
            await queue.wait(second, timeout=1)
        self.assertEqual(queue.status(second)["state"], "rejected")
        self.assertEqual([call[1]["message"] for call in self.calls], ["safe retry", "safe retry", "refused"])

    async def test_expired_or_no_longer_authorized_items_are_not_dispatched(self):
        now = [100.]
        queue = self.outbox(clock=lambda: now[0], max_age=30)
        expired = await self.enqueue(queue, "expired")
        now[0] += 32
        no_longer_allowed = await self.enqueue(queue, "target changed")
        self.allowed = False
        self.online = True
        with self.assertRaises(DeliveryFailed):
            await queue.wait(expired, timeout=1)
        with self.assertRaises(DeliveryFailed):
            await queue.wait(no_longer_allowed, timeout=1)
        self.assertEqual(queue.status(expired)["state"], "expired")
        self.assertEqual(queue.status(no_longer_allowed)["state"], "blocked")
        self.assertEqual(self.calls, [])

    async def test_local_attachment_survives_source_cleanup_and_restart(self):
        queue = self.outbox()
        original = self.path.parent / "出站附件.txt"
        original.write_bytes(b"attachment payload")
        content = [{"type": "file", "data": {"file": original.as_uri(), "name": original.name}}]
        entry_id = await self.enqueue(queue, content)
        original.unlink()
        await queue.close()

        def check_snapshot(params):
            segment, = params["message"]
            retained_path = Path(url2pathname(urlsplit(segment["data"]["file"]).path))
            self.assertEqual(retained_path.read_bytes(), b"attachment payload")
            self.assertEqual(retained_path.name, "出站附件.txt")
            return {"message_id": 21}

        self.outcomes = [check_snapshot]
        self.online = True
        queue = self.outbox()
        queue.start()
        self.assertEqual(await queue.wait(entry_id, timeout=1), {"message_id": 21})
        self.assertFalse((queue.media / entry_id).exists())

    async def test_pending_cancel_and_guard_recheck_do_not_send(self):
        queue = self.outbox()
        entry_id = await self.enqueue(queue, "cancelled offline")
        self.assertTrue(queue.cancel(entry_id))
        self.assertFalse(queue.cancel(entry_id))
        self.online = True
        with self.assertRaises(DeliveryFailed) as blocked:
            await queue.submit("send_private_msg", {"user_id": 123, "message": "guarded"},
                               live_guard=lambda: False)
        self.assertEqual(blocked.exception.state, "blocked")
        self.assertEqual(queue.status(entry_id)["state"], "cancelled")
        self.assertEqual(self.calls, [])

    async def test_adapter_closing_in_flight_retains_uncertain_state(self):
        queue = self.outbox()
        entry_id = await self.enqueue(queue, "in flight")
        started = asyncio.Event()

        async def send(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        queue.send = send
        self.online = True
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertFalse(queue.cancel(entry_id))
        await queue.close()
        queue = self.outbox()
        self.assertEqual(queue.status(entry_id)["state"], "uncertain")


if __name__ == "__main__":
    unittest.main()
