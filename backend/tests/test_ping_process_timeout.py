"""ping_host must not wait forever on its child process.

A ping child the event loop never reaped kept run_ping_checks hanging, and
APScheduler skipped every later run (2026-10-29, 2026-11-18).
"""
import asyncio
from unittest.mock import MagicMock, patch

from utils import ping


class _StuckProcess:
    pid = 999_999_999
    returncode = None

    def __init__(self):
        self.kill = MagicMock()

    async def communicate(self):
        await asyncio.sleep(3600)


async def test_ping_host_gives_up_on_a_process_that_never_finishes():
    proc = _StuckProcess()

    async def fake_exec(*args, **kwargs):
        return proc

    with patch.object(ping, "PING_PROCESS_GRACE_S", 0.05), \
         patch("asyncio.create_subprocess_exec", new=fake_exec):
        result = await asyncio.wait_for(ping.ping_host("192.0.2.1", timeout=0.0), 2)

    assert result == (False, None)
    proc.kill.assert_called_once()


async def test_ping_host_tolerates_a_process_that_is_already_gone():
    proc = _StuckProcess()
    proc.kill.side_effect = ProcessLookupError

    async def fake_exec(*args, **kwargs):
        return proc

    with patch.object(ping, "PING_PROCESS_GRACE_S", 0.05), \
         patch("asyncio.create_subprocess_exec", new=fake_exec):
        assert await ping.ping_host("192.0.2.1", timeout=0.0) == (False, None)
