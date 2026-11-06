"""Host deadlines over real ownership and inert event-blocked workers."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from livekit.agents import ToolError

from host_execution import HostExecutionLock
from skill_tools import SkillTools


def host(tmp_path, timeout=0.04):
    tools = SkillTools()
    tools._execution_lock = HostExecutionLock(tmp_path / "host.lock")
    tools._host_jobs = set()
    tools._host_action_timeout_seconds = timeout
    return tools


async def drain(tools):
    if tools._host_jobs:
        await asyncio.wait_for(
            asyncio.gather(*tuple(tools._host_jobs), return_exceptions=True), 1
        )
    await asyncio.sleep(0)


async def test_contended_queue_returns_retryable_timeout_without_actuation(tmp_path):
    tools = host(tmp_path)
    owner = HostExecutionLock(tmp_path / "host.lock")
    await owner.acquire()
    effects = []
    call = asyncio.create_task(tools._run_host_action(effects.append, "input"))
    try:
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert done, "host queue has no response deadline"
        with pytest.raises(ToolError) as failure:
            await call
        payload = json.loads(str(failure.value))
        assert payload["status"] == "host_queue_timeout"
        assert payload["retryable"] is True
        assert failure.value.effect_started is False
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        owner.release()
        await drain(tools)
    assert effects == []
    assert await tools._run_host_action(lambda: "next") == "next"


async def test_running_timeout_retains_ownership_until_worker_settles(tmp_path):
    tools = host(tmp_path)
    started, finish = Event(), Event()
    effects = []

    def blocked():
        started.set()
        finish.wait(2)
        effects.append("first")
        return "late success"

    call = asyncio.create_task(tools._run_host_action(blocked))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        done, _ = await asyncio.wait({call}, timeout=0.2)
        assert done, "running host worker has no response deadline"
        with pytest.raises(ToolError) as failure:
            await call
        payload = json.loads(str(failure.value))
        assert payload["status"] == "host_action_timeout"
        assert payload["retryable"] is False
        assert payload["outcome"] == "uncertain"
        assert failure.value.effect_started is True
        assert tools._execution_lock.locked()
        with pytest.raises(ToolError) as queued:
            await tools._run_host_action(effects.append, "second")
        assert queued.value.effect_started is False
        assert effects == []
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        finish.set()
        await drain(tools)
    assert effects == ["first"]
    assert not tools._execution_lock.locked()
    assert tools._host_jobs == set()
    assert await tools._run_host_action(lambda: "next") == "next"


async def test_cancelled_running_caller_cannot_release_worker_ownership(tmp_path):
    tools = host(tmp_path, timeout=1)
    started, finish = Event(), Event()

    def blocked():
        started.set()
        finish.wait(2)

    call = asyncio.create_task(tools._run_host_action(blocked))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert tools._execution_lock.locked()
        # Even cancellation of tracked async tasks cannot kill a native thread.
        for task in tuple(tools._host_jobs):
            task.cancel()
        await asyncio.sleep(0.01)
        assert tools._execution_lock.locked(), "native worker lost ownership"
    finally:
        finish.set()
        await drain(tools)
    assert not tools._execution_lock.locked()


async def test_success_and_worker_error_release_host(tmp_path):
    tools = host(tmp_path, timeout=1)
    assert await tools._run_host_action(lambda a, *, b: a + b, 2, b=3) == 5

    def broken():
        raise TimeoutError("native failure")

    with pytest.raises(TimeoutError, match="native failure"):
        await tools._run_host_action(broken)
    await drain(tools)
    assert not tools._execution_lock.locked()


async def test_cancelled_queue_never_runs_after_owner_releases(tmp_path):
    tools = host(tmp_path)
    await tools._execution_lock.acquire()
    effects = []
    call = asyncio.create_task(tools._run_host_action(effects.append, "abandoned"))
    await asyncio.sleep(0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    tools._execution_lock.release()
    await asyncio.sleep(0.05)
    assert effects == []
    assert await tools._run_host_action(lambda: "next") == "next"


async def test_executor_queue_timeout_does_not_execute_abandoned_input(tmp_path):
    tools = host(tmp_path)
    finish = Event()
    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(executor)
    occupied = loop.run_in_executor(None, finish.wait, 2)
    effects = []
    try:
        with pytest.raises(ToolError) as failure:
            await tools._run_host_action(effects.append, "abandoned")
        assert failure.value.effect_started is False
    finally:
        finish.set()
        await occupied
        await drain(tools)
        executor.shutdown(wait=True)
    assert effects == []
    assert not tools._execution_lock.locked()


async def test_cancel_when_lock_becomes_available_never_executes_input(tmp_path):
    tools = host(tmp_path)
    await tools._execution_lock.acquire()
    effects = []
    call = asyncio.create_task(tools._run_host_action(effects.append, "abandoned"))
    await asyncio.sleep(0)
    tools._execution_lock.release()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await drain(tools)
    assert effects == []
    assert await tools._run_host_action(lambda: "next") == "next"


async def test_preflight_failure_never_executes_and_releases_host(tmp_path):
    tools = host(tmp_path)
    effects = []

    def preflight():
        raise ValueError("stale target")

    with pytest.raises(ValueError, match="stale target"):
        await tools._run_host_action(effects.append, "input", _preflight=preflight)
    await drain(tools)
    assert effects == []
    assert not tools._execution_lock.locked()
