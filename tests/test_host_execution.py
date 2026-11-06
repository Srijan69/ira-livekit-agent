import asyncio

import pytest


async def test_separate_owners_serialize_same_desktop(tmp_path):
    from host_execution import HostExecutionLock

    first = HostExecutionLock(tmp_path / "host.lock")
    second = HostExecutionLock(tmp_path / "host.lock")
    await first.acquire()
    waiting = asyncio.create_task(second.acquire())
    await asyncio.sleep(0.03)
    assert not waiting.done()
    first.release()
    assert await asyncio.wait_for(waiting, 1)
    second.release()


async def test_cancelled_waiter_does_not_leak_ownership(tmp_path):
    from host_execution import HostExecutionLock

    first = HostExecutionLock(tmp_path / "host.lock")
    second = HostExecutionLock(tmp_path / "host.lock")
    await first.acquire()
    waiting = asyncio.create_task(second.acquire())
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    first.release()
    await asyncio.wait_for(second.acquire(), 1)
    second.release()
