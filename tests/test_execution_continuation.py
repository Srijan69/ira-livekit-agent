"""Hermes-style no-tool continuation stays bound and has a strict retry limit."""

import pytest
from livekit.agents import ToolError, llm
from livekit.agents.voice.speech_handle import SpeechHandle

from request_scope import RequestScope
from request_scope_livekit import RequestScopeTools


class Session:
    def __init__(self):
        self.replies = []

    def generate_reply(self, **kwargs):
        self.replies.append(kwargs)
        return SpeechHandle.create()


def report(facade, request, text):
    handle = SpeechHandle.create()
    facade.bind_speech(handle, request)
    handle._item_added([llm.ChatMessage(role="assistant", content=[text])])
    handle._mark_done()
    return handle


@pytest.mark.parametrize(
    "text",
    [
        "I'll open Calculator, Boss.",
        "I can't open apps or use your browser, Boss.",
    ],
)
async def test_default_policy_recovers_no_tool_promise_or_false_capability_refusal(
    text,
):
    scope = RequestScope()
    facade = RequestScopeTools(scope)
    facade._session = Session()
    facade._available_tools = {"open_app"}
    request = scope.begin_request("Open Calculator", source_id="human:1")
    await facade.review_completed_speech(report(facade, request, text))
    assert len(facade._session.replies) == 1
    assert "instructions" in facade._session.replies[0]
    assert "user_input" not in facade._session.replies[0]
    assert scope.require(request.id) == request
    await facade.review_completed_speech(report(facade, request, text))
    assert len(facade._session.replies) == 1
    assert scope.require(request.id) == request  # Unfinished after retry exhausted.
    await facade.aclose()


@pytest.mark.parametrize(
    "request_text,text",
    [
        ("Explain how Calculator works", "I can't open Calculator."),
        ("Don't open Calculator", "I'll open Calculator."),
        ("Open Calculator", "Which application did you mean?"),
        ("Open Calculator", "Calculator needs you to sign in."),
        ("Open Calculator", "I can't help with harmful requests."),
    ],
)
async def test_no_continuation_for_nonaction_clarification_or_real_blocker(
    request_text, text
):
    scope = RequestScope()
    facade = RequestScopeTools(scope)
    facade._session = Session()
    facade._available_tools = {"open_app"}
    request = scope.begin_request(request_text, source_id="human:1")
    await facade.review_completed_speech(report(facade, request, text))
    assert not facade._session.replies
    await facade.aclose()


async def test_stopped_old_or_interrupted_request_never_continues():
    scope = RequestScope()
    facade = RequestScopeTools(scope)
    facade._session = Session()
    facade._available_tools = {"open_app"}
    request = scope.begin_request("Open Calculator", source_id="human:1")
    handle = report(facade, request, "I'll open it, Boss.")
    scope.stop()
    await facade.review_completed_speech(handle)
    assert not facade._session.replies
    with pytest.raises(ToolError):
        scope.require(request.id)
    await facade.aclose()


def test_real_procedural_tool_history_can_continue_but_errors_cannot():
    from execution_continuation import needs_tool_continuation

    history = [
        {
            "tool": "run_skill",
            "outcome": "returned",
            "detail": '{"status":"procedure_ready", "instructions":"Open using tools"}',
        }
    ]
    assert needs_tool_continuation(
        "Open Calculator", "I'll open it, Boss.", history, {"open_app"}
    )
    history[0]["outcome"] = "error"
    assert not needs_tool_continuation(
        "Open Calculator", "I'll open it, Boss.", history, {"open_app"}
    )
