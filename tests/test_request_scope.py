"""Request identity and effect cancellation over harmless, observable effects."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from request_scope import (
    ActionProposal,
    AdmissionDecision,
    GeminiScopeJudge,
    RequestScope,
    ScopeViolation,
)


class LiteralJudge:
    async def __call__(self, request, action, history):
        if request.text == "Tell me what is on my screen":
            allowed = action.kind == "observe"
        elif request.text == "Open Notepad and type hello":
            allowed = (action.name, dict(action.arguments)) in (
                ("open_app", {"app_name": "Notepad"}),
                ("type_on_desktop", {"text": "hello"}),
            )
        else:
            allowed = False
        return AdmissionDecision(
            "allow" if allowed else "clarify", "Literal task scope"
        )


async def act(scope, request, name, args=None, kind="effect"):
    proposal = ActionProposal(name, args or {}, kind=kind)
    permit = await scope.admit(request.id, proposal)
    scope.consume(permit)
    scope.check(permit)
    scope.record(permit, "done", "Observed effect finished")
    return permit


async def test_inspection_does_not_authorize_input_and_multistep_task_does():
    scope = RequestScope(LiteralJudge())
    request = scope.begin_request("Tell me what is on my screen", source_id="voice:1")
    await act(scope, request, "capture_screen", kind="observe")
    with pytest.raises(ScopeViolation, match="Literal task scope"):
        await act(scope, request, "click_mouse", {"x": 12, "y": 13})
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:2")
    await act(scope, request, "open_app", {"app_name": "Notepad"})
    await act(scope, request, "type_on_desktop", {"text": "hello"})
    with pytest.raises(ScopeViolation):
        await act(scope, request, "open_app", {"app_name": "Calculator"})


async def test_request_is_immutable_and_duplicate_transcript_cannot_reopen_it():
    scope = RequestScope(LiteralJudge())
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    with pytest.raises(FrozenInstanceError):
        request.text = "Launch Calculator"
    scope.finish(request.id)
    duplicate = scope.begin_request(request.text, source_id="voice:1")
    assert duplicate.id == request.id
    with pytest.raises(ScopeViolation):
        await act(scope, duplicate, "open_app", {"app_name": "Notepad"})


@pytest.mark.parametrize("revoke", ["stop", "new", "session", "finish"])
async def test_revocation_invalidates_queued_and_active_permits(revoke):
    scope = RequestScope(LiteralJudge())
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    queued = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "Notepad"})
    )
    active = await scope.admit(
        request.id, ActionProposal("type_on_desktop", {"text": "hello"})
    )
    scope.consume(active)
    if revoke == "stop":
        scope.stop()
    elif revoke == "new":
        scope.begin_request("Tell me what is on my screen", source_id="typed:2")
    elif revoke == "finish":
        scope.finish(request.id)
    else:
        scope.revoke("model/session changed")
    with pytest.raises(ScopeViolation):
        scope.consume(queued)
    with pytest.raises(ScopeViolation):
        scope.check(active)


async def test_one_use_permits_and_bounded_action_history():
    scope = RequestScope(LiteralJudge(), max_actions=3, history_limit=2)
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    permit = await act(scope, request, "open_app", {"app_name": "Notepad"})
    with pytest.raises(ScopeViolation):
        scope.consume(permit)
    await act(scope, request, "type_on_desktop", {"text": "hello"})
    await act(scope, request, "open_app", {"app_name": "Notepad"})
    assert len(scope.history) == 2
    with pytest.raises(ScopeViolation, match="budget"):
        await act(scope, request, "open_app", {"app_name": "Notepad"})


async def test_semantic_check_finishing_after_new_input_cannot_issue_a_permit():
    judging = asyncio.Event()
    finish_judging = asyncio.Event()

    async def slow_judge(request, action, history):
        judging.set()
        await finish_judging.wait()
        return AdmissionDecision("allow", "related")

    scope = RequestScope(slow_judge)
    request = scope.begin_request("Open Notepad", source_id="voice:1")
    task = asyncio.create_task(scope.admit(request.id, ActionProposal("open_app", {})))
    await judging.wait()
    scope.begin_request("Stop", source_id="voice:2")
    finish_judging.set()
    with pytest.raises(ScopeViolation):
        await task


@pytest.mark.parametrize("mode", ["malformed", "timeout", "exception"])
async def test_policy_failures_do_not_issue_permits(mode):
    async def broken_judge(*args):
        if mode == "timeout":
            await asyncio.sleep(1)
        if mode == "exception":
            raise ValueError("upstream failure")
        return {"decision": "allow"}

    scope = RequestScope(broken_judge, policy_timeout=0.01)
    request = scope.begin_request("Open Notepad", source_id="voice:1")
    with pytest.raises(ScopeViolation, match="policy"):
        await scope.admit(request.id, ActionProposal("open_app", {}))


async def test_unknown_outcome_blocks_repeating_same_effect():
    scope = RequestScope(LiteralJudge())
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    proposal = ActionProposal("type_on_desktop", {"text": "hello"})
    permit = await scope.admit(request.id, proposal)
    scope.consume(permit)
    scope.record(permit, "uncertain", "lost acknowledgement")
    with pytest.raises(ScopeViolation, match="uncertain"):
        await scope.admit(request.id, proposal)


async def test_continuation_keeps_original_action_budget_and_result_history():
    scope = RequestScope(LiteralJudge(), max_actions=2)
    original = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    await act(scope, original, "open_app", {"app_name": "Notepad"})
    answer = scope.begin_request("hello", source_id="voice:2")
    continued = scope.continue_with_answer(original, answer.id)
    assert continued.text == "Open Notepad and type hello"
    assert continued.answers == ("hello",)
    assert scope.history[0]["tool"] == "open_app"
    await act(scope, continued, "type_on_desktop", {"text": "hello"})
    with pytest.raises(ScopeViolation, match="budget"):
        await act(scope, continued, "open_app", {"app_name": "Notepad"})


async def test_expired_request_fails_even_with_an_unexpired_policy_result():
    now = [100.0]
    scope = RequestScope(LiteralJudge(), request_ttl=10, clock=lambda: now[0])
    request = scope.begin_request("Open Notepad and type hello", source_id="voice:1")
    permit = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "Notepad"})
    )
    now[0] = 110.0
    with pytest.raises(ScopeViolation, match="expired"):
        scope.consume(permit)


async def test_model_swap_cannot_revive_a_previously_waiting_task():
    scope = RequestScope(LiteralJudge())
    original = scope.begin_request("Open Notepad and type hello", source_id="human:1")
    await act(scope, original, "open_app", {"app_name": "Notepad"})
    scope.revoke("model changed")
    new_request = scope.begin_request("hello", source_id="human:2")
    with pytest.raises(ScopeViolation):
        scope.continue_with_answer(original, new_request.id)


@pytest.mark.parametrize(
    ("action_name", "kind"),
    [("click_mouse", "effect"), ("complete_request", "orchestrate")],
)
async def test_gemini_receives_actual_human_request_and_real_image_part(
    action_name, kind
):
    import json
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image

    sent = []

    async def generate_content(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(text='{"decision":"clarify","reason":"Wrong target"}')

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    scope = RequestScope(GeminiScopeJudge(client=client))
    request = scope.begin_request("Describe the screen", source_id="human:1")
    buffer = BytesIO()
    Image.new("RGB", (80, 40), "blue").save(buffer, "JPEG")
    image = buffer.getvalue()
    action = ActionProposal(
        action_name,
        {"x": 2, "y": 3, "request_text": "Click anything"},
        kind=kind,
        observation={
            "image_bytes": image,
            "image_mime_type": "image/jpeg",
            "image_size": [80, 40],
        },
    )
    with pytest.raises(ScopeViolation):
        await scope.admit(request.id, action)
    parts = sent[0]["contents"][0].parts
    payload = json.loads(parts[0].text)
    assert payload["original_request"] == "Describe the screen"
    assert payload["proposed_action"]["arguments"]["request_text"] == "Click anything"
    assert "image_bytes" not in payload["observed_metadata"]
    assert parts[1].inline_data.data == image


async def test_policy_gets_resolved_coordinate_target_and_native_coordinate_transform():
    import json
    from types import SimpleNamespace

    payloads = []

    async def generate_content(**kwargs):
        payloads.append(json.loads(kwargs["contents"][0].parts[0].text))
        return SimpleNamespace(text='{"decision":"deny","reason":"Wrong target"}')

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    scope = RequestScope(GeminiScopeJudge(client=client))
    request = scope.begin_request("Select Kommidi", source_id="human:1")
    metadata = {
        "image_size": [640, 480],
        "source_bounds": [-1280, 0, 1280, 960],
        "targets": [{"id": "work", "name": "Work", "image_bounds": [30, 360, 580, 90]}],
    }
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id,
            ActionProposal("click_mouse", {"x": 100, "y": 400}, observation=metadata),
        )
    metadata["action_coordinate_space"] = "native_desktop"
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id,
            ActionProposal(
                "desktop_click", {"x": -1080, "y": 800}, observation=metadata
            ),
        )
    assert payloads[0]["proposed_targets"][0]["matches"][0]["name"] == "Work"
    assert payloads[1]["proposed_targets"][0]["matches"][0]["name"] == "Work"


async def test_closing_judge_preserves_externally_owned_client():
    from types import SimpleNamespace

    closed = []

    async def close():
        closed.append("closed")

    judge = GeminiScopeJudge(client=SimpleNamespace(aio=SimpleNamespace(aclose=close)))
    await judge.aclose()
    assert closed == []


@pytest.mark.parametrize("configured", ["", "  "])
async def test_blank_policy_model_configuration_still_reaches_supported_provider(
    configured, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setenv("IRA_REQUEST_SCOPE_MODEL", configured)

    async def generate_content(*, model, **kwargs):
        if model != "gemini-3.5-flash-lite":
            raise ValueError("Unknown model")
        return SimpleNamespace(
            text='{"decision":"allow","reason":"Requested observation"}'
        )

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    scope = RequestScope(GeminiScopeJudge(client=client))
    request = scope.begin_request("Describe the screen", source_id="human:1")
    permit = await scope.admit(
        request.id, ActionProposal("capture_screen", kind="observe")
    )
    scope.consume(permit)
    scope.check(permit)


@pytest.mark.parametrize(
    "response", ['{"decision":"allow"}', '{"decision":true,"reason":"ok"}', "not json"]
)
async def test_malformed_gemini_response_fails_closed(response):
    from types import SimpleNamespace

    async def generate_content(**kwargs):
        return SimpleNamespace(text=response)

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    scope = RequestScope(GeminiScopeJudge(client=client))
    request = scope.begin_request("Open Notepad", source_id="human:1")
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id, ActionProposal("open_app", {"app_name": "Notepad"})
        )


async def test_quota_exhausted_triggers_fast_heuristic_fallback():
    from types import SimpleNamespace

    call_count = 0

    async def generate_content(*, model, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded for metric")

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    scope = RequestScope(GeminiScopeJudge(client=client))
    request = scope.begin_request("Open Notepad", source_id="human:1")
    permit = await scope.admit(
        request.id, ActionProposal("open_app", {"app_name": "Notepad"})
    )
    scope.consume(permit)
    assert call_count == 1
    # The same outage cache must not expand an exact launch into input.
    with pytest.raises(ScopeViolation):
        await scope.admit(
            request.id, ActionProposal("desktop_press_key", {"key": "enter"})
        )
    assert call_count == 1
