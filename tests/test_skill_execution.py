import asyncio
import threading
import time
from dataclasses import replace

import pytest

from skill_execution import (
    CheckOutcome,
    SkillExecutionService,
    ToolOutcome,
    ToolRegistry,
)
from skill_format import Action, Check, Skill, SkillInput, Step
from skill_runs import SkillRunStore
from skill_store import SkillStore
from telemetry import SessionStatusReporter


def skill(*steps, **kwargs):
    return Skill(
        "Open music",
        "Open and observe music",
        tuple(steps),
        allowed_tools=("open_app",),
        state="enabled",
        **kwargs,
    )


def step(tool="open_app", args=(("app_name", "Music"),), check=None):
    return Step(
        1,
        "Open music",
        action=Action("tool", tool=tool, args=args),
        check=check or Check("window_title", "Music"),
    )


async def observed(check):
    return CheckOutcome(True, f"observed {check.value}")


def reporter(events=None):
    return SessionStatusReporter(
        run_id="session",
        sink=lambda _: None,
        observer=events.append if events is not None else None,
    )


@pytest.mark.asyncio
async def test_every_step_and_input_is_validated_before_any_action(tmp_path):
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        return ToolOutcome(True)

    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    registry = ToolRegistry({"open_app": open_app})
    invalid = [
        (skill(step(), step("missing")), None),
        (skill(step(), step(args=(("bad_argument", "x"),))), None),
        (skill(step()), {"undeclared": "x"}),
        (replace(skill(step()), allowed_tools=()), None),
        (replace(skill(step()), state="draft"), None),
        (skill(step(), step(check=Check("unknown", "x"))), None),
        (skill(step(), replace(step(), check=None)), None),
        (skill(step(args=(("app_name", "{missing}"),))), None),
        (skill(step(), inputs=(SkillInput("required", "Required"),)), None),
    ]
    for candidate, inputs in invalid:
        result = await service.run(candidate, registry, reporter(), inputs=inputs)
        assert result.status == "error"
        assert not result.verified
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_result,check_passes",
    [
        ("Could not open app: simulated failure", True),
        (ToolOutcome(False, "unavailable"), True),
        (ToolOutcome(True, "issued", "action submitted"), False),
    ],
)
async def test_failed_or_untyped_outcome_and_failed_check_stop_later_steps(
    tmp_path, first_result, check_passes
):
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        return first_result

    async def verify(check):
        return CheckOutcome(check_passes, "window missing")

    service = SkillExecutionService(
        verifier=verify, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    result = await service.run(
        skill(step(), step()), ToolRegistry({"open_app": open_app}), reporter()
    )
    assert result.status == "error"
    assert result.completed_steps == 0
    assert result.failed_step == 1
    assert calls == ["Music"]


@pytest.mark.asyncio
async def test_verified_run_keeps_pinned_version_and_observation_history(tmp_path):
    events = []
    store = SkillRunStore(tmp_path / "runs.db")

    def open_app(app_name: str):
        assert app_name == "Spotify"
        return ToolOutcome(True, "opened", "window id 42")

    original = skill(
        step(args=(("app_name", "{app}"),)),
        version=7,
        inputs=(SkillInput("app", "Application", "Spotify"),),
        done_check=Check("page_contains", "Playing"),
    )
    service = SkillExecutionService(verifier=observed, run_store=store)
    result = await service.run(
        original,
        ToolRegistry({"open_app": open_app}),
        reporter(events),
        run_id="delivery-1",
    )
    assert result.status == "done" and result.verified
    assert result.completed_steps == result.total_steps == 1
    record = store.get("delivery-1")
    assert record["skill_version"] == 7
    assert "version: 7" in record["skill_markdown"]
    assert record["steps"][0]["evidence"] == "window id 42"
    assert record["steps"][0]["check_evidence"] == "observed Music"
    assert record["done_evidence"] == "observed Playing"
    assert all(event["run_id"] == "session" for event in events)
    assert events[-1]["skill_run_id"] == "delivery-1"
    assert events[-1]["outcome"] == "done"


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_repeat_running_or_finished_actions(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def open_app(app_name: str):
        calls.append(app_name)
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=store)
    registry = ToolRegistry({"open_app": open_app})
    first = asyncio.create_task(
        service.run(skill(step()), registry, reporter(), run_id="same")
    )
    await entered.wait()
    duplicate = asyncio.create_task(
        service.run(skill(step()), registry, reporter(), run_id="same")
    )
    release.set()
    assert await first == await duplicate
    assert (
        await service.run(skill(step()), registry, reporter(), run_id="same")
    ).status == "done"
    restarted = SkillExecutionService(verifier=observed, run_store=store)
    assert (
        await restarted.run(skill(step()), registry, reporter(), run_id="same")
    ).status == "done"
    assert calls == ["Music"]


@pytest.mark.asyncio
async def test_stop_pending_action_remains_uncertain_after_late_success(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def open_app(app_name: str):
        calls.append(app_name)
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    events = []
    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=store)
    run = asyncio.create_task(
        service.run(
            skill(step(), step()),
            ToolRegistry({"open_app": open_app}),
            reporter(events),
            run_id="stopping",
        )
    )
    await entered.wait()
    assert service.stop("stopping")
    result = await asyncio.wait_for(run, 1)
    assert result.status == "uncertain"
    assert service.execution_lock.locked()
    release.set()
    async with service.execution_lock:
        pass
    assert calls == ["Music"]
    assert store.get("stopping")["status"] == "uncertain"
    assert "DONE" not in [event["state"] for event in events]


@pytest.mark.asyncio
async def test_timeout_of_sync_action_retains_desktop_ownership_until_settled(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        entered.set()
        release.wait(timeout=3)
        return ToolOutcome(True)

    service = SkillExecutionService(
        verifier=observed,
        run_store=SkillRunStore(tmp_path / "runs.db"),
        action_timeout=0.03,
    )
    run = asyncio.create_task(
        service.run(
            skill(step(), step()), ToolRegistry({"open_app": open_app}), reporter()
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        result = await asyncio.wait_for(run, 1)
        assert result.status == "uncertain"
        assert service.execution_lock.locked()
        direct_entered = asyncio.Event()

        async def direct_action():
            async with service.execution_lock:
                direct_entered.set()

        direct = asyncio.create_task(direct_action())
        await asyncio.sleep(0)
        assert not direct_entered.is_set()
    finally:
        release.set()
    await asyncio.wait_for(direct, 1)
    assert calls == ["Music"]


@pytest.mark.asyncio
async def test_stop_queued_run_is_cancelled_without_touching_desktop(tmp_path):
    calls = []
    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    await service.execution_lock.acquire()
    try:
        run = asyncio.create_task(
            service.run(
                skill(step()),
                ToolRegistry({"open_app": lambda app_name: calls.append(app_name)}),
                reporter(),
                run_id="queued",
            )
        )
        await asyncio.sleep(0)
        assert service.stop("queued")
        result = await asyncio.wait_for(run, 1)
        assert result.status == "cancelled"
        assert calls == []
    finally:
        service.execution_lock.release()


@pytest.mark.asyncio
async def test_run_deadline_bounds_polling_and_never_issues_next_action(tmp_path):
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        return ToolOutcome(True)

    async def missing(check):
        return CheckOutcome(False, "absent")

    service = SkillExecutionService(
        verifier=missing,
        run_store=SkillRunStore(tmp_path / "runs.db"),
        run_timeout=0.2,
        poll_interval=0.005,
    )
    result = await asyncio.wait_for(
        service.run(
            skill(step(check=Check("window_title", "Music", 1)), step()),
            ToolRegistry({"open_app": open_app}),
            reporter(),
        ),
        1,
    )
    assert result.status == "error"
    assert calls == ["Music"]
    assert not result.verified


def test_draft_validation_checks_structure_without_requiring_runtime_inputs(tmp_path):
    def open_app(app_name: str):
        return ToolOutcome(True)

    service = SkillExecutionService(
        verifier=observed,
        run_store=SkillRunStore(tmp_path / "runs.db"),
        supported_checks=("window_title",),
    )
    registry = ToolRegistry({"open_app": open_app})
    candidate = replace(
        skill(
            step(args=(("app_name", "{app}"),)),
            inputs=(SkillInput("app", "Application"),),
        ),
        state="draft",
    )
    service.validate(candidate, registry, require_enabled=False)
    with pytest.raises(ValueError, match="unsupported"):
        service.validate(
            replace(candidate, steps=(step(check=Check("looks_like", "music")),)),
            registry,
            require_enabled=False,
        )


def test_registry_validates_types_without_calling_tools():
    def click(x: int, enabled: bool = True):
        raise AssertionError("must not execute during validation")

    registry = ToolRegistry({"click": click})
    assert registry.validate("click", {"x": "42", "enabled": "false"}) == {
        "x": 42,
        "enabled": False,
    }
    for args in (
        {"x": "four"},
        {"x": "1", "enabled": "maybe"},
        {"x": 1, "extra": 2},
        {},
    ):
        with pytest.raises(ValueError):
            registry.validate("click", args)


@pytest.mark.asyncio
async def test_stop_during_running_notification_prevents_new_action(tmp_path):
    calls = []
    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )

    def event(event):
        if event["state"] == "RUNNING":
            service.stop("before-call")

    status = SessionStatusReporter(sink=lambda _: None, observer=event)
    result = await service.run(
        skill(step()),
        ToolRegistry({"open_app": lambda app_name: calls.append(app_name)}),
        status,
        run_id="before-call",
    )
    assert result.status == "cancelled"
    assert calls == []


@pytest.mark.asyncio
async def test_deadline_expired_during_notification_prevents_new_action(tmp_path):
    calls = []
    service = SkillExecutionService(
        verifier=observed,
        run_store=SkillRunStore(tmp_path / "runs.db"),
        run_timeout=0.02,
    )

    def event(event):
        if event["state"] == "RUNNING":
            time.sleep(0.03)

    status = SessionStatusReporter(sink=lambda _: None, observer=event)
    result = await service.run(
        skill(step()),
        ToolRegistry({"open_app": lambda app_name: calls.append(app_name)}),
        status,
    )
    assert result.status == "error"
    assert calls == []


@pytest.mark.asyncio
async def test_semantically_invalid_later_arguments_are_preflighted(tmp_path):
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        return ToolOutcome(True)

    def valid_name(app_name: str):
        if not app_name.strip():
            raise ValueError("Application name is empty")

    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    result = await service.run(
        skill(step(), step(args=(("app_name", " "),))),
        ToolRegistry({"open_app": open_app}, validators={"open_app": valid_name}),
        reporter(),
    )
    assert result.status == "error"
    assert calls == []


@pytest.mark.asyncio
async def test_unexpected_exception_after_tool_started_is_uncertain(tmp_path):
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        raise RuntimeError("connection lost after submission")

    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=store)
    result = await service.run(
        skill(step(), step()), ToolRegistry({"open_app": open_app}), reporter()
    )
    assert result.status == "uncertain"
    assert not result.verified
    assert calls == ["Music"]


@pytest.mark.asyncio
async def test_caller_cancellation_is_persisted_and_holds_lock_until_action_settles(
    tmp_path,
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def open_app(app_name: str):
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=store)
    run = asyncio.create_task(
        service.run(
            skill(step()),
            ToolRegistry({"open_app": open_app}),
            reporter(),
            run_id="sdk-cancel",
        )
    )
    await entered.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert store.get("sdk-cancel")["status"] == "uncertain"
    assert service.execution_lock.locked()
    release.set()
    async with service.execution_lock:
        pass
    assert store.get("sdk-cancel")["status"] == "uncertain"


@pytest.mark.asyncio
async def test_second_live_service_does_not_recover_first_services_running_work(
    tmp_path,
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def open_app(app_name: str):
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    store = SkillRunStore(tmp_path / "runs.db")
    first = SkillExecutionService(verifier=observed, run_store=store)
    task = asyncio.create_task(
        first.run(
            skill(step()),
            ToolRegistry({"open_app": open_app}),
            reporter(),
            run_id="live",
        )
    )
    await entered.wait()
    try:
        second = SkillExecutionService(
            verifier=observed, run_store=SkillRunStore(store.path)
        )
        assert second.status("live")["status"] == "running"
    finally:
        release.set()
    assert (await task).status == "done"


@pytest.mark.asyncio
async def test_async_execution_never_runs_sqlite_io_on_voice_loop(
    tmp_path, monkeypatch
):
    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=store)
    owner_thread = threading.get_ident()
    threads = []

    def instrument(method):
        def invoke(*args, **kwargs):
            threads.append(threading.get_ident())
            return method(*args, **kwargs)

        return invoke

    for name in ("get", "create", "update", "record_step"):
        monkeypatch.setattr(store, name, instrument(getattr(store, name)))

    result = await service.run(
        skill(step()),
        ToolRegistry({"open_app": lambda app_name: ToolOutcome(True)}),
        reporter(),
    )
    assert result.status == "done"
    assert threads and owner_thread not in threads


@pytest.mark.asyncio
async def test_service_shutdown_persists_stop_and_rejects_new_work(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def open_app(app_name: str):
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    registry = ToolRegistry({"open_app": open_app})
    task = asyncio.create_task(
        service.run(skill(step()), registry, reporter(), run_id="closing")
    )
    await entered.wait()
    try:
        await asyncio.wait_for(service.aclose(), 1)
        assert service.status("closing")["status"] == "uncertain"
        assert service.execution_lock.locked()
        with pytest.raises(ValueError, match="closed"):
            await service.run(skill(step()), registry, reporter())
    finally:
        release.set()
    assert (await task).status == "uncertain"
    async with service.execution_lock:
        pass


@pytest.mark.asyncio
async def test_cancelled_verification_cannot_be_replaced_by_late_success(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    def open_app(app_name: str):
        calls.append(app_name)
        return ToolOutcome(True)

    async def verify(check):
        entered.set()
        await release.wait()
        return CheckOutcome(True, "late observation")

    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=verify, run_store=store)
    task = asyncio.create_task(
        service.run(
            skill(step(), step()),
            ToolRegistry({"open_app": open_app}),
            reporter(),
            run_id="verify-stop",
        )
    )
    await entered.wait()
    service.stop("verify-stop")
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled"
    assert service.execution_lock.locked()
    release.set()
    async with service.execution_lock:
        pass
    assert store.get("verify-stop")["status"] == "cancelled"
    assert calls == ["Music"]


@pytest.mark.asyncio
async def test_editing_saved_skill_does_not_change_an_active_runs_version(tmp_path):
    skills = SkillStore(tmp_path / "skills")
    original = skills.save(skill(step()), source="compiled")
    entered, release = asyncio.Event(), asyncio.Event()

    async def open_app(app_name: str):
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    runs = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(verifier=observed, run_store=runs)
    task = asyncio.create_task(
        service.run(
            original, ToolRegistry({"open_app": open_app}), reporter(), run_id="frozen"
        )
    )
    await entered.wait()
    changed = skills.save(
        replace(original, description="Edited during execution"), source="edited"
    )
    release.set()
    result = await task
    assert changed.version == 2
    assert result.skill_version == 1
    assert result.status == "done"
    assert "Edited during execution" not in runs.get("frozen")["skill_markdown"]


@pytest.mark.asyncio
async def test_custom_filename_identity_is_preserved_in_run_history_and_events(
    tmp_path,
):
    events = []
    service = SkillExecutionService(
        verifier=observed, run_store=SkillRunStore(tmp_path / "runs.db")
    )
    result = await service.run(
        skill(step()),
        ToolRegistry({"open_app": lambda app_name: ToolOutcome(True)}),
        reporter(events),
        run_id="custom",
        skill_slug="custom-file",
    )
    assert result.skill_slug == "custom-file"
    assert service.status("custom")["skill_slug"] == "custom-file"
    assert {event["skill_slug"] for event in events} == {"custom-file"}
    with pytest.raises(ValueError, match="slug"):
        await service.run(
            skill(step()), ToolRegistry(), reporter(), skill_slug="../escape"
        )
