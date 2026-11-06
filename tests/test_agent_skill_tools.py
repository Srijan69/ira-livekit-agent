import asyncio
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from livekit.agents import ToolError

from agent import Assistant
from skill_format import Action, Check, Skill, Step
from skill_store import SkillStore
from telemetry import SessionStatusReporter


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


def page_skill():
    return Skill(
        name="Read example",
        description="Opens the example page.",
        allowed_tools=("browse_to",),
        state="enabled",
        steps=(
            Step(
                1,
                "Open",
                action=Action(
                    "tool", tool="browse_to", args=(("url", "https://example.com"),)
                ),
                check=Check("page_contains", "Example"),
            ),
        ),
    )


async def test_draft_skill_cannot_touch_browser(tmp_path):
    store = SkillStore(tmp_path)
    store.save(replace(page_skill(), state="draft"), source="dictated")
    browser = SimpleNamespace(navigate=lambda url: pytest.fail("draft executed"))
    assistant = Assistant(skills=store, browser=browser)
    with pytest.raises(ToolError, match="enabled"):
        await assistant.run_skill(None, "read-example")


async def test_voice_and_persisted_run_agree_on_failed_check(tmp_path):
    store = SkillStore(tmp_path)
    store.save(page_skill(), source="compiled")
    browser = SimpleNamespace(
        navigate=lambda url: "Navigation requested", observe=lambda kind, value: False
    )
    assistant = Assistant(skills=store, browser=browser)
    with pytest.raises(ToolError):
        await assistant.run_skill(None, "read-example")
    history = assistant._ensure_skill_runtime().run_store.list()
    assert history[0]["status"] == "error"
    assert history[0]["completed_steps"] == 0


async def test_verified_skill_completes_and_preserves_call_correlation(tmp_path):
    store = SkillStore(tmp_path)
    store.save(page_skill(), source="compiled")
    browser = SimpleNamespace(
        navigate=lambda url: "Navigation requested", observe=lambda kind, value: True
    )
    events = []
    assistant = Assistant(
        skills=store,
        browser=browser,
        reporter=SessionStatusReporter(observer=events.append, sink=lambda _: None),
    )
    context = SimpleNamespace(
        function_call=SimpleNamespace(call_id="abc", name="run_skill")
    )
    result = await assistant.run_skill(context, "read-example")
    assert "completed" in result.lower()
    assert events[-1]["state"] == "DONE"
    assert events[-1]["call_id"] == "abc"
    assert events[-1]["skill_version"] == 1


async def test_browser_error_raises_tool_error_and_output_is_bounded():
    def broken(_url):
        raise RuntimeError("private driver stack")

    assistant = Assistant(
        browser=SimpleNamespace(navigate=broken, read_page=lambda: "x" * 5000)
    )
    with pytest.raises(ToolError):
        await assistant.browse_to(None, "https://example.com")
    assert len(await assistant.read_page(None)) <= 2000


async def test_open_app_operational_failure_is_not_a_successful_tool_return():
    with (
        patch(
            "agent._launch_via_start_menu",
            side_effect=RuntimeError("simulated failure"),
        ),
        pytest.raises(ToolError, match="Could not open"),
    ):
        await Assistant().open_app(None, "Notepad")


@pytest.mark.parametrize(
    "name",
    [
        "cmd /c whoami",
        "powershell -Command whoami",
        "calc & whoami",
        "python -c print(1)",
    ],
)
async def test_app_launcher_does_not_accept_shell_commands(name):
    with patch("agent._launch_via_start_menu") as launch:
        with pytest.raises(ToolError):
            await Assistant().open_app(None, name)
        launch.assert_not_called()


async def test_cancelled_direct_action_waiting_for_desktop_never_executes(tmp_path):
    assistant = Assistant(skills=SkillStore(tmp_path))
    await assistant._execution_lock.acquire()
    actions = []
    task = asyncio.create_task(assistant._run_host_action(actions.append, "abandoned"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assistant._execution_lock.release()
    await asyncio.sleep(0.03)
    assert actions == []


@pytest.mark.parametrize(
    "url", ["https://example.com:bad", "https://example.com:999999", "https://bad host"]
)
async def test_malformed_later_url_rejected_before_first_action(tmp_path, url):
    store = SkillStore(tmp_path)
    second = replace(
        page_skill().steps[0],
        number=2,
        action=Action("tool", tool="browse_to", args=(("url", url),)),
    )
    store.save(replace(page_skill(), steps=(page_skill().steps[0], second)))
    actions = []
    assistant = Assistant(
        skills=store,
        browser=SimpleNamespace(
            navigate=actions.append, observe=lambda kind, value: True
        ),
    )
    with pytest.raises(ToolError):
        await assistant.run_skill(None, "read-example")
    assert actions == []


async def test_cancellation_records_its_own_run_when_a_newer_run_is_queued(tmp_path):
    store = SkillStore(tmp_path)
    store.save(page_skill())
    started, release = Event(), Event()
    calls = []

    def navigate(url):
        calls.append(url)
        if len(calls) == 1:
            started.set()
            release.wait(3)

    assistant = Assistant(
        skills=store,
        browser=SimpleNamespace(navigate=navigate, observe=lambda kind, value: True),
    )

    def context(call_id):
        return SimpleNamespace(
            function_call=SimpleNamespace(call_id=call_id, name="run_skill")
        )

    first = asyncio.create_task(assistant.run_skill(context("first"), "read-example"))
    assert await asyncio.to_thread(started.wait, 2)
    second = asyncio.create_task(assistant.run_skill(context("second"), "read-example"))
    for _ in range(100):
        if assistant._last_skill_run_id.endswith(":second"):
            break
        await asyncio.sleep(0.01)
    assert assistant._last_skill_run_id.endswith(":second")
    first.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await first
    finally:
        release.set()
    await second
    row = store.catalog()[0]
    assert (row["use_count"], row["failure_count"], row["success_count"]) == (2, 1, 1)


async def test_list_skills_and_view_skill(tmp_path):
    store = SkillStore(tmp_path)
    store.save(page_skill(), source="compiled")
    assistant = Assistant(skills=store)

    listing = await assistant.list_skills(None)
    assert "read-example" in listing
    assert "enabled" in listing

    view = await assistant.view_skill(None, "read-example")
    assert "Read example" in view
    assert "browse_to" in view
    assert "https://example.com" in view

    missing = await assistant.view_skill(None, "non-existent")
    assert "not found" in missing.lower()
