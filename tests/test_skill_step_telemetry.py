"""Display events describe observed steps without conflating tool and run completion."""

import json
from types import SimpleNamespace

import pytest

from skill_execution import (
    CheckOutcome,
    SkillExecutionService,
    ToolOutcome,
    ToolRegistry,
)
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from telemetry import SessionStatusReporter


def setup(tmp_path, *, final_ok=True):
    events = []
    effects = []

    async def open_app(app_name: str):
        effects.append(app_name)
        return ToolOutcome(True)

    async def verify(check):
        return CheckOutcome(final_ok if check.value == "final" else True, "observed")

    service = SkillExecutionService(
        verifier=verify, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    reporter = SessionStatusReporter(
        run_id="session", sink=lambda _: None, observer=events.append
    ).for_tool(
        SimpleNamespace(function_call=SimpleNamespace(call_id="call", name="run_skill"))
    )
    return service, ToolRegistry({"open_app": open_app}), reporter, events, effects


def action(app_name="Music"):
    return Step(
        1,
        "Open",
        action=Action("tool", tool="open_app", args=(("app_name", app_name),)),
        check=Check("window_title", "Music"),
    )


def recipe(*steps, **kwargs):
    return Skill(
        "Open music",
        "Open and observe",
        tuple(steps),
        allowed_tools=("open_app",),
        state="enabled",
        **kwargs,
    )


@pytest.mark.parametrize("final_ok", [True, False])
async def test_steps_finish_only_after_observation_and_final_check_is_run_level(
    tmp_path, final_ok
):
    service, registry, reporter, events, effects = setup(tmp_path, final_ok=final_ok)
    result = await service.run(
        recipe(action(), action(), done_check=Check("window_title", "final")),
        registry,
        reporter,
        run_id="skill-1",
    )
    assert result.status == ("done" if final_ok else "error")
    assert effects == ["Music", "Music"]
    steps = [
        (e["step_index"], e["step_tool"], e["state"], e["step_outcome"])
        for e in events
        if "step_tool" in e
    ]
    assert steps == [
        (i, "open_app", state, outcome)
        for i in (1, 2)
        for state, outcome in (
            ("RUNNING", "running"),
            ("VERIFYING", "verifying"),
            ("RUNNING", "done"),
        )
    ]
    assert all(e["step_count"] == 2 and type(e["step_index"]) is int for e in events)
    assert all(e["tool_name"] == "run_skill" and e["call_id"] == "call" for e in events)
    assert all(
        e["skill_run_id"] == "skill-1" and e["source"] == "skill" for e in events
    )
    assert "step_outcome" not in events[-1]


async def test_waiting_and_resumed_steps_keep_the_original_run_and_actual_tool(
    tmp_path,
):
    service, registry, reporter, events, effects = setup(tmp_path)
    skill = recipe(
        Step(1, "Ask", action=Action("ask", param="app", text="Which app?")),
        action("{app}"),
        inputs=(SkillInput("app", "Application"),),
    )
    try:
        waiting = await service.run(skill, registry, reporter, run_id="skill-1")
        assert waiting.status == "waiting" and effects == []
        assert events[-1]["step_tool"] == "ask"
        assert events[-1]["step_outcome"] == "waiting"
        done = await service.answer("skill-1", "Music", question_id=waiting.question_id)
        assert done.status == "done"
        assert [
            (e["step_index"], e["step_tool"])
            for e in events
            if e.get("step_outcome") == "done"
        ] == [(1, "ask"), (2, "open_app")]
        assert all(e["call_id"] == "call" for e in events)
    finally:
        await service.aclose()


async def test_failed_tool_has_explicit_step_error_and_no_step_success(tmp_path):
    service, _, reporter, events, _ = setup(tmp_path)

    async def open_app(app_name: str):
        return ToolOutcome(False, "Could not open")

    result = await service.run(
        recipe(action()), ToolRegistry({"open_app": open_app}), reporter
    )
    assert result.status == "error"
    assert events[-1]["step_tool"] == "open_app"
    assert events[-1]["step_outcome"] == "error"
    assert not any(e.get("step_outcome") == "done" for e in events)


@pytest.mark.parametrize("raises,expected", [(True, "uncertain"), (False, "error")])
async def test_long_failures_publish_bounded_terminal_metadata_without_exception_text(
    tmp_path, raises, expected
):
    service, _, reporter, events, _ = setup(tmp_path)

    async def open_app(app_name: str):
        private = "private-driver-output " + "失敗" * 3000
        if raises:
            raise RuntimeError(private)
        return ToolOutcome(False, private)

    result = await service.run(
        recipe(action()), ToolRegistry({"open_app": open_app}), reporter
    )
    assert result.status == expected
    terminal = events[-1]
    assert terminal["outcome"] == terminal["step_outcome"] == expected
    assert terminal["step_tool"] == "open_app"
    assert len(terminal["detail"]) <= 1024
    assert len(json.dumps(terminal).encode("utf-8")) <= 4096
    assert "private-driver-output" not in terminal["detail"]
