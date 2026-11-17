import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from unittest.mock import AsyncMock

from test_bili_activity import core
from bili_activity_test.reminders import OwnerReminders, SHANGHAI


class OwnerReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_reminders_queue_while_offline_and_reconcile_actual_receipt(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            reminders = OwnerReminders(store)
            scope = "test:FriendMessage:12345"
            now = datetime(2026, 11, 12, 9, tzinfo=SHANGHAI).timestamp()
            first = reminders.create(scope, "41", "2026-11-12 09:01", "带伞", now=now)
            second = reminders.create(scope, "42", "2026-11-12 09:02", "带钥匙", now=now)
            send = AsyncMock()
            enqueue = AsyncMock(side_effect=[{"fishhome_queue_id": "first"}, {"fishhome_queue_id": "second"}])
            queued = {}
            for _ in range(2):
                await reminders.dispatch_due(scope, send, now=now + 121, ready=lambda: False,
                                             enqueue=enqueue, lookup=queued.get)
            self.assertEqual([call.args[2] for call in enqueue.await_args_list],
                             [reminders._key(scope, first), reminders._key(scope, second)])
            send.assert_not_awaited()
            self.assertTrue(reminders.cancel(scope, second, lambda entry: entry == "second"))
            queued["first"] = {"state": "sent", "message_id": "-67"}
            await reminders.dispatch_due(scope, send, now=now + 122, enqueue=enqueue, lookup=queued.get)
            rows = {item["id"]: item for item in reminders.list(scope)}
            self.assertEqual(rows[first]["message_id"], "-67")
            self.assertEqual(rows[second]["status"], "cancelled")
            self.assertEqual(enqueue.await_count, 2)

    async def test_create_cancel_dispatch_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store = core.Store(folder)
            now = datetime(2026, 11, 12, 9, tzinfo=SHANGHAI).timestamp()
            scope = "test:FriendMessage:12345"
            reminders = OwnerReminders(store)
            ident = reminders.create(scope, "11", "2026-11-12 09:01", "带伞", now=now)
            self.assertEqual(ident, reminders.create(scope, "11", "2026-11-12 09:01", "带伞", now=now))
            cancelled = reminders.create(scope, "12", "2026-11-12 09:01", "另一个", now=now)
            self.assertTrue(reminders.cancel(scope, cancelled))
            send = AsyncMock(return_value={"message_id": "-42"})
            await reminders.dispatch_due(scope, send, now=now)
            send.assert_not_awaited()
            store.close()

            with closing(core.Store(folder)) as store:
                reminders = OwnerReminders(store)
                await reminders.dispatch_due("test:FriendMessage:67890", send, now=now + 61)
                await reminders.dispatch_due(scope, send, now=now + 61, ready=lambda: False)
                send.assert_not_awaited()
                await reminders.dispatch_due(scope, send, now=now + 61)
                await reminders.dispatch_due(scope, send, now=now + 120)
                send.assert_awaited_once_with(scope, "提醒：带伞")
                rows = {item["id"]: item for item in reminders.list(scope)}
                self.assertEqual(rows[ident]["status"], "sent")
                self.assertEqual(rows[ident]["message_id"], "-42")
                self.assertEqual(rows[cancelled]["status"], "cancelled")

    async def test_unknown_receipt_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder, closing(core.Store(folder)) as store:
            reminders = OwnerReminders(store)
            scope = "test:FriendMessage:12345"
            now = datetime(2026, 11, 12, 9, tzinfo=SHANGHAI).timestamp()
            reminders.create(scope, "13", "2026-11-12 09:01", "核对任务", now=now)
            send = AsyncMock(side_effect=TimeoutError)
            await reminders.dispatch_due(scope, send, now=now + 61)
            await reminders.dispatch_due(scope, send, now=now + 120)
            send.assert_awaited_once()
            self.assertEqual(reminders.list(scope)[0]["status"], "uncertain")
