"""Voice tools exercise temporary stores, an injected clock, and fake rooms."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import agent as agent_module
from agent import Assistant
from system_status import SystemStatusUnavailableError


def test_tasks_clock_and_system_tools_are_registered():
    names = {tool.info.name for tool in Assistant().tools}
    assert {
        "add_task",
        "list_tasks",
        "complete_task",
        "current_time",
        "system_status",
    } <= names


async def test_current_time_reads_clock_each_time_with_local_offset():
    now = datetime(
        2026, 10, 15, 23, 59, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    assistant = Assistant(clock=lambda: now)
    first = await assistant.current_time(None)
    assert first["local_time"] == "2026-10-15T23:59:00+05:30"
    assert first["utc_time"] == "2026-10-15T18:29:00Z"
    assert first["weekday"] == "Thursday"
    now += timedelta(minutes=2)
    assert (await assistant.current_time(None))[
        "local_time"
    ] == "2026-10-16T00:01:00+05:30"


async def test_task_tools_persist_and_only_notify_after_writes(tmp_path):
    from tasks import TaskStore

    store = TaskStore(tmp_path / "tasks.db")
    events = []
    assistant = Assistant(tasks=store, publish_event=events.append)
    try:
        saved = json.loads(
            await assistant.add_task(None, "Revision", "2026-10-16T09:00:00+05:30")
        )
        assert saved["ok"] is True
        assert saved["task"]["title"] == "Revision"
        assert saved["task"]["due_at"] == "2026-10-16T03:30:00Z"
        assert events == [{"type": "tasks.changed"}]
        listed = json.loads(await assistant.list_tasks(None))
        assert listed[0]["title"] == "Revision"
        assert listed[0]["due_at"] == "2026-10-16T03:30:00Z"
        assert len(events) == 1
        assert (
            await assistant.complete_task(None, int(listed[0]["id"]))
            == "Completed, boss."
        )
        assert store.upcoming() == []
        assert events == [{"type": "tasks.changed"}, {"type": "tasks.changed"}]
        store.close()
        assert store.upcoming() == []
    finally:
        store.close()


async def test_invalid_task_or_unknown_completion_never_reports_success(tmp_path):
    from tasks import TaskStore

    store = TaskStore(tmp_path / "tasks.db")
    events = []
    assistant = Assistant(tasks=store, publish_event=events.append)
    try:
        result = await assistant.add_task(None, "Revision", "tomorrow at nine")
        assert "not added" in result.lower()
        assert store.upcoming() == []
        assert "not found" in (await assistant.complete_task(None, 987)).lower()
        assert events == []
    finally:
        store.close()


async def test_event_failure_does_not_undo_a_saved_task(tmp_path):
    from tasks import TaskStore

    store = TaskStore(tmp_path / "tasks.db")

    def broken(_event):
        raise RuntimeError("room gone")

    try:
        assistant = Assistant(tasks=store, publish_event=broken)
        assert await assistant.add_task(None, "Read chapter") == "Added, boss."
        assert [task.title for task in store.upcoming()] == ["Read chapter"]
    finally:
        store.close()


async def test_tasks_changed_uses_reliable_events_topic():
    published = []
    sent = asyncio.Event()

    async def publish_data(payload, *, topic, reliable):
        published.append((json.loads(payload), topic, reliable))
        sent.set()

    room = SimpleNamespace(
        isconnected=lambda: True,
        local_participant=SimpleNamespace(publish_data=publish_data),
    )
    publisher = agent_module._room_event_publisher(room)
    publisher({"type": "tasks.changed"})
    await asyncio.wait_for(sent.wait(), timeout=1)
    assert published == [({"type": "tasks.changed"}, "ira.events", True)]


async def test_system_tool_reports_metrics_and_absent_battery():
    result = {
        "cpu_percent": 12.0,
        "memory_percent": 58.0,
        "disk_percent": 41.0,
        "battery_percent": None,
        "power_plugged": None,
    }
    assistant = Assistant(system_reader=SimpleNamespace(read=lambda: result))
    assert json.loads(await assistant.system_status(None)) == result


async def test_system_tool_does_not_invent_stats_when_unavailable():
    def broken():
        raise SystemStatusUnavailableError("backend unavailable")

    assistant = Assistant(system_reader=SimpleNamespace(read=broken))
    assert "unavailable" in (await assistant.system_status(None)).lower()


async def test_task_simulation_mocks_are_isolated_and_support_followup():
    mocks = agent_module._simulation_tool_mocks({})
    assert await mocks["add_task"]("Test task", None) == "Added, boss."
    listed = json.loads(await mocks["list_tasks"]())
    assert listed[0]["title"] == "Test task"
    assert await mocks["complete_task"](int(listed[0]["id"])) == "Completed, boss."
    assert "no upcoming tasks" in (await mocks["list_tasks"]()).lower()
    assert (
        "no upcoming tasks"
        in (await agent_module._simulation_tool_mocks({})["list_tasks"]()).lower()
    )
