import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deploy.bili_activity import resource_capacity


@unittest.skipUnless(os.name == "nt", "Windows service resource gate")
class ResourceCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_media_waits_for_memory_and_for_previous_local_slot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            memory = SimpleNamespace(available=2 * 1024**3)
            entered = asyncio.Event()

            async def process():
                async with resource_capacity.media_slot():
                    entered.set()

            with patch.object(resource_capacity.psutil, "virtual_memory", return_value=memory):
                first = asyncio.create_task(process())
                await asyncio.sleep(.1)
                self.assertFalse(entered.is_set())
                memory.available = 4 * 1024**3
                await asyncio.wait_for(first, 3)
                self.assertTrue(entered.is_set())
                entered.clear()
                async with resource_capacity.media_slot():
                    second = asyncio.create_task(process())
                    await asyncio.sleep(.1)
                    self.assertFalse(entered.is_set())
                await asyncio.wait_for(second, 2)
                self.assertTrue(entered.is_set())
