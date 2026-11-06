"""Clarifications yield a turn without replaying or trusting stale host state."""

import asyncio
import json
import threading
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
from telemetry import SessionStatusReporter


def ask(name, text=None):
    return Step(
        1, "Clarify", action=Action("ask", param=name, text=text or f"Which {name}?")
    )


def effect(value, *, tool="write", check=None, **extra):
    return Step(
        1,
        "Write value",
        action=Action("tool", tool=tool, args=(("value", value), *extra.items())),
        check=check or Check("page_contains", "ready"),
    )


def recipe(*steps, inputs=("first",), **kwargs):
    return Skill(
        "Clarification",
        "Ask then act",
        tuple(steps),
        inputs=tuple(SkillInput(name, name) for name in inputs),
        allowed_tools=("write",),
        state="enabled",
        **kwargs,
    )


def reporter(events=None):
    return SessionStatusReporter(
        sink=lambda _: None, observer=events.append if events is not None else None
    )


def setup(tmp_path, *, verifier=None, **kwargs):
    effects = []

    async def write(value: str):
        effects.append(value)
        return ToolOutcome(True, f"wrote {value}", f"value: {value}")

    async def observed(check):
        return CheckOutcome(True, check.value)

    store = SkillRunStore(tmp_path / "runs.db")
    service = SkillExecutionService(
        verifier=verifier or observed, run_store=store, **kwargs
    )
    return service, ToolRegistry({"write": write}), effects, store


async def terminal(store, run_id):
    async def poll():
        while store.get(run_id)["status"] not in {
            "done",
            "error",
            "cancelled",
            "uncertain",
        }:
            await asyncio.sleep(0.005)
        return store.get(run_id)

    return await asyncio.wait_for(poll(), 1)


@pytest.mark.asyncio
async def test_ordered_questions_resume_frozen_source_without_replaying_actions(
    tmp_path,
):
    service, registry, effects, store = setup(tmp_path)
    original = recipe(
        effect("start"),
        ask("first"),
        effect("{first}"),
        ask("second"),
        effect("{second}"),
        inputs=("first", "second"),
        version=7,
    )
    try:
        first = await asyncio.wait_for(
            service.run(original, registry, reporter(), run_id="ordered"), 1
        )
        assert first.status == "waiting"
        assert first.completed_steps == 1 and first.total_steps == 5
        assert (
            first.input_name == "first"
            and first.question == "Which first?"
            and first.question_id
        )
        assert store.get("ordered")["question_id"] == first.question_id
        changed = replace(original, steps=(effect("changed"),), version=8)
        duplicate = await service.run(changed, registry, reporter(), run_id="ordered")
        assert duplicate == first
        second = await service.answer("ordered", "one", question_id=first.question_id)
        assert second.status == "waiting" and second.input_name == "second"
        assert second.question_id != first.question_id and second.completed_steps == 3
        result = await service.answer("ordered", "two", question_id=second.question_id)
        assert (
            result.status == "done" and result.completed_steps == 5 and result.verified
        )
        assert result.skill_version == 7
        assert effects == ["start", "one", "two"]
        assert store.get("ordered")["question_id"] == ""
        assert "changed" not in store.get("ordered")["skill_markdown"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_step", [effect("{first}"), ask("first", "Is {first} correct?")]
)
async def test_required_input_used_before_ask_rejects_entire_plan(tmp_path, bad_step):
    service, registry, effects, _ = setup(tmp_path)
    result = await service.run(
        recipe(effect("start"), bad_step, ask("first"), effect("{first}")),
        registry,
        reporter(),
    )
    assert result.status == "error" and effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late_step",
    [
        effect("constant", tool="missing"),
        effect("constant", check=Check("unknown", "ready")),
        effect("constant", unknown="extra"),
    ],
)
async def test_invalid_constant_after_question_rejects_before_effects(
    tmp_path, late_step
):
    service, registry, effects, _ = setup(tmp_path)
    result = await service.run(
        recipe(effect("start"), ask("first"), late_step), registry, reporter()
    )
    assert result.status == "error" and effects == []


@pytest.mark.asyncio
async def test_invalid_answer_preflights_all_newly_bound_actions_and_can_retry(
    tmp_path,
):
    service, _, effects, store = setup(tmp_path)

    async def write(value: int):
        effects.append(value)
        return ToolOutcome(True)

    registry = ToolRegistry({"write": write})
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("1"), effect("{first}")),
            registry,
            reporter(),
            run_id="retry",
        )
        assert waiting.status == "waiting"
        with pytest.raises(ValueError):
            await service.answer(
                "retry", "private-invalid-number", question_id=waiting.question_id
            )
        assert store.get("retry")["status"] == "waiting" and effects == []
        assert store.get("retry")["question_id"] == waiting.question_id
        result = await service.answer("retry", "42", question_id=waiting.question_id)
        assert result.status == "done" and effects == [1, 42]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_stale_and_duplicate_answers_cannot_fill_a_later_question(tmp_path):
    service, registry, effects, _ = setup(tmp_path)
    try:
        first = await service.run(
            recipe(
                ask("first"),
                ask("second"),
                effect("{first}:{second}"),
                inputs=("first", "second"),
            ),
            registry,
            reporter(),
            run_id="stale",
        )
        assert first.status == "waiting"
        second = await service.answer("stale", "one", question_id=first.question_id)
        with pytest.raises(ValueError):
            await service.answer("stale", "duplicate", question_id=first.question_id)
        with pytest.raises(ValueError):
            await service.answer("stale", "unknown", question_id="wrong")
        assert effects == []
        result = await service.answer("stale", "two", question_id=second.question_id)
        assert result.status == "done" and effects == ["one:two"]
        with pytest.raises(ValueError):
            await service.answer("stale", "closed", question_id=second.question_id)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_answer_runs_action_once(tmp_path):
    service, _, effects, _ = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def write(value: str):
        effects.append(value)
        entered.set()
        await release.wait()
        return ToolOutcome(True)

    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            ToolRegistry({"write": write}),
            reporter(),
            run_id="concurrent",
        )
        assert waiting.status == "waiting"
        first = asyncio.create_task(
            service.answer("concurrent", "once", question_id=waiting.question_id)
        )
        await entered.wait()
        with pytest.raises(ValueError):
            await service.answer("concurrent", "twice", question_id=waiting.question_id)
        release.set()
        assert (await first).status == "done" and effects == ["once"]
    finally:
        release.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_waiting_releases_host_lock_and_resume_rechecks_state_before_effect(
    tmp_path,
):
    checks = []
    current = "ready"

    async def verify(check):
        checks.append(check.value)
        return CheckOutcome(current == check.value, current)

    lock = asyncio.Lock()
    service, registry, effects, _ = setup(
        tmp_path, verifier=verify, execution_lock=lock
    )
    try:
        waiting = await service.run(
            recipe(effect("start"), ask("first"), effect("{first}")),
            registry,
            reporter(),
            run_id="lock",
        )
        assert waiting.status == "waiting" and not lock.locked()
        await lock.acquire()
        current = "changed"
        resume = asyncio.create_task(
            service.answer("lock", "later", question_id=waiting.question_id)
        )
        await asyncio.sleep(0.02)
        assert effects == ["start"] and not resume.done()
        lock.release()
        result = await resume
        assert result.status == "error" and effects == ["start"]
        assert checks == ["ready", "ready"] and not lock.locked()
    finally:
        if lock.locked():
            lock.release()
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_method", ["stop", "aclose"])
async def test_stop_and_close_terminate_waiting_without_late_answer(
    tmp_path, stop_method
):
    service, registry, effects, store = setup(tmp_path)
    waiting = await service.run(
        recipe(ask("first"), effect("{first}")), registry, reporter(), run_id="stop"
    )
    assert waiting.status == "waiting"
    if stop_method == "stop":
        assert service.stop("stop")
    else:
        await service.aclose()
    with pytest.raises(ValueError):
        await service.answer("stop", "late", question_id=waiting.question_id)
    record = await terminal(store, "stop")
    assert record["status"] == "cancelled" and not record["question_id"]
    assert effects == [] and not service.execution_lock.locked()
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeouts",
    [
        {"clarification_timeout": 0.04},
        {"run_timeout": 0.3, "clarification_timeout": 10},
    ],
)
async def test_waiting_expires_without_another_call_and_preserves_run_deadline(
    tmp_path, timeouts
):
    service, registry, effects, store = setup(tmp_path, **timeouts)
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            registry,
            reporter(),
            run_id="expiry",
        )
        assert waiting.status == "waiting"
        record = await terminal(store, "expiry")
        assert (
            record["status"] == "error" and effects == [] and not record["question_id"]
        )
        with pytest.raises(ValueError):
            await service.answer("expiry", "late", question_id=waiting.question_id)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_answers_are_absent_from_history_events_and_errors(tmp_path):
    events = []
    service, registry, _, store = setup(tmp_path)
    secret = "customer-secret-token"
    try:
        waiting = await service.run(
            recipe(
                ask("first"),
                effect("prefix {first}", check=Check("page_contains", "{first}")),
                ask("second", "Continue for {first}?"),
                effect("{second}"),
                inputs=("first", "second"),
            ),
            registry,
            reporter(events),
            run_id="privacy",
        )
        assert waiting.status == "waiting"
        second = await service.answer(
            "privacy", secret, question_id=waiting.question_id
        )
        assert second.status == "waiting" and secret not in second.question
        await service.answer("privacy", "done", question_id=second.question_id)
        assert secret not in json.dumps(store.get("privacy"))
        assert secret not in json.dumps(events)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_invalid_answer_error_does_not_echo_validator_input(tmp_path):
    service, _, _, store = setup(tmp_path)

    async def write(value: str):
        return ToolOutcome(True)

    def validate(value):
        raise ValueError(f"Invalid private value: {value}")

    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            ToolRegistry({"write": write}, validators={"write": validate}),
            reporter(),
            run_id="private-error",
        )
        assert waiting.status == "waiting"
        with pytest.raises(ValueError) as error:
            await service.answer(
                "private-error", "private-entry", question_id=waiting.question_id
            )
        assert "private-entry" not in str(error.value)
        assert "private-entry" not in json.dumps(store.get("private-error"))
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_symbolic_validator_checks_constant_fields_and_braced_answers(tmp_path):
    service, _, effects, _ = setup(tmp_path)

    async def write(value: str, count: int = 1):
        effects.append(value)
        return ToolOutcome(True)

    def validate(value, count=1, *, _symbolic=frozenset()):
        if "count" not in _symbolic and count < 1:
            raise ValueError("count must be positive")
        if "value" not in _symbolic and "{" in value:
            raise ValueError("braces are invalid")

    registry = ToolRegistry({"write": write}, validators={"write": validate})
    invalid = await service.run(
        recipe(effect("start"), ask("first"), effect("{first}", count="-1")),
        registry,
        reporter(),
    )
    assert invalid.status == "error" and effects == []
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            registry,
            reporter(),
            run_id="braces",
        )
        assert waiting.status == "waiting"
        with pytest.raises(ValueError):
            await service.answer(
                "braces", "{do_not_defer}", question_id=waiting.question_id
            )
        assert effects == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelling_terminal_watcher_does_not_cancel_paused_run(tmp_path):
    service, registry, effects, store = setup(tmp_path)
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            registry,
            reporter(),
            run_id="watch",
        )
        watcher = asyncio.create_task(service.wait_for_terminal("watch"))
        await asyncio.sleep(0)
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        assert store.get("watch")["status"] == "waiting"
        watcher = asyncio.create_task(service.wait_for_terminal("watch"))
        finished = await service.answer("watch", "go", question_id=waiting.question_id)
        assert await watcher == finished
        assert await service.wait_for_terminal("watch") == finished
        assert effects == ["go"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_page_value_binds_json_fields_without_answer_injection(tmp_path):
    checks = []

    async def verify(check):
        checks.append(json.loads(check.value))
        return CheckOutcome(True)

    service, registry, _, _ = setup(
        tmp_path, verifier=verify, supported_checks=("page_value",)
    )
    check = Check("page_value", '{"selector":"#name","value":"{first}"}')
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}", check=check)),
            registry,
            reporter(),
            run_id="json",
        )
        assert waiting.status == "waiting"
        result = await service.answer(
            "json", 'O\'Reilly "example" \\ {literal}', question_id=waiting.question_id
        )
        assert result.status == "done"
        assert checks == [
            {"selector": "#name", "value": 'O\'Reilly "example" \\ {literal}'}
        ]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_same_input_can_be_asked_again_without_rebinding_prior_observation(
    tmp_path,
):
    checks = []

    async def verify(check):
        checks.append(check.value)
        return CheckOutcome(True)

    service, registry, effects, _ = setup(tmp_path, verifier=verify)
    try:
        first = await service.run(
            recipe(
                ask("first"),
                effect("{first}", check=Check("page_contains", "{first}")),
                ask("first"),
                effect("{first}"),
            ),
            registry,
            reporter(),
            run_id="repeat",
        )
        second = await service.answer("repeat", "one", question_id=first.question_id)
        result = await service.answer("repeat", "two", question_id=second.question_id)
        assert result.status == "done" and effects == ["one", "two"]
        assert checks == ["one", "one", "ready"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_runtime_failure_after_answer_does_not_persist_derived_private_text(
    tmp_path,
):
    events = []
    service, _, _, store = setup(tmp_path)

    async def write(value: int):
        raise RuntimeError(f"failed for derived value {value}")

    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            ToolRegistry({"write": write}),
            reporter(events),
            run_id="private-runtime",
        )
        result = await service.answer(
            "private-runtime", "0001234", question_id=waiting.question_id
        )
        assert result.status == "uncertain"
        assert "1234" not in result.message
        assert "1234" not in json.dumps(store.get("private-runtime"))
        assert "1234" not in json.dumps(events)
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["x" * 1001, "bad\x00question"])
async def test_invalid_question_size_and_control_characters_fail_before_effects(
    tmp_path, question
):
    service, registry, effects, _ = setup(tmp_path)
    try:
        result = await service.run(
            recipe(effect("start"), ask("first", question), effect("{first}")),
            registry,
            reporter(),
        )
        assert result.status == "error" and effects == []
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["x" * 4001, "bad\x00answer"])
async def test_invalid_answer_size_and_control_characters_leave_prompt_retryable(
    tmp_path, answer
):
    service, registry, effects, store = setup(tmp_path)
    try:
        waiting = await service.run(
            recipe(ask("first"), effect("{first}")),
            registry,
            reporter(),
            run_id="bounded",
        )
        with pytest.raises(ValueError):
            await service.answer("bounded", answer, question_id=waiting.question_id)
        assert store.get("bounded")["status"] == "waiting" and effects == []
        result = await service.answer(
            "bounded", "valid", question_id=waiting.question_id
        )
        assert result.status == "done" and effects == ["valid"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_answer_arriving_during_waiting_publication_preserves_both_callers(
    tmp_path,
):
    written, release = threading.Event(), threading.Event()

    class PublicationStore(SkillRunStore):
        def update(self, run_id, **fields):
            result = super().update(run_id, **fields)
            if fields.get("status") == "waiting":
                written.set()
                release.wait(timeout=2)
            return result

    store = PublicationStore(tmp_path / "publication.db")
    service = SkillExecutionService(verifier=lambda _: True, run_store=store)
    effects = []

    async def write(value: str):
        effects.append(value)
        return ToolOutcome(True)

    first = asyncio.create_task(
        service.run(
            recipe(ask("first"), effect("{first}")),
            ToolRegistry({"write": write}),
            reporter(),
            run_id="publication",
        )
    )
    response = None
    try:
        assert await asyncio.to_thread(written.wait, 1)
        question_id = store.get("publication")["question_id"]
        response = asyncio.create_task(
            service.answer("publication", "value", question_id=question_id)
        )
        await asyncio.sleep(0)
        release.set()
        waiting, done = await asyncio.wait_for(asyncio.gather(first, response), 1)
        assert waiting.status == "waiting" and done.status == "done"
        assert effects == ["value"]
    finally:
        release.set()
        await service.aclose()
        await asyncio.gather(
            first, *([response] if response else []), return_exceptions=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["check", "until"])
async def test_nested_action_checks_are_preflighted_before_prior_effects(
    tmp_path, field
):
    service, registry, effects, _ = setup(tmp_path)
    malformed = replace(
        ask("first"),
        action=replace(ask("first").action, **{field: Check("unknown", "x")}),
    )
    try:
        result = await service.run(
            recipe(effect("start"), malformed, effect("{first}")), registry, reporter()
        )
        assert result.status == "error" and effects == []
    finally:
        await service.aclose()
