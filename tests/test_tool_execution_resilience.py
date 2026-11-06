from types import SimpleNamespace

import pytest
from livekit.agents import Agent, RunContext, ToolError, function_tool
from livekit.agents.voice.speech_handle import SpeechHandle

from request_scope import AdmissionDecision, RequestScope
from request_scope_livekit import RequestScopeTools


class MockJudge:
    def __init__(self):
        self.calls = []

    async def __call__(self, request, action, history):
        self.calls.append((request, action))
        # Allow anything within requested scope or confirmed
        allowed = "Notepad" in request.text or "Phonk" in request.text
        return AdmissionDecision("allow" if allowed else "deny", "Mock check")


class SampleAgent(Agent):
    def __init__(self):
        self.opened_apps = []
        self.played_playlists = []
        super().__init__(instructions="Sample test agent")

    @function_tool
    async def open_app(self, context: RunContext, app_name: str) -> str:
        self.opened_apps.append(app_name)
        return f"Opened {app_name}"

    @function_tool
    async def spotify_play_playlist(self, context: RunContext, name: str) -> str:
        self.played_playlists.append(name)
        return f"Playing {name}"

    @function_tool
    async def view_skill(self, context: RunContext, name: str) -> str:
        return f"# Skill: {name}"

    @function_tool
    async def list_workflows(self, context: RunContext) -> str:
        return "workflow-1, workflow-2"


def setup_agent():
    agent = SampleAgent()
    judge = MockJudge()
    scope = RequestScope(judge)
    facade = RequestScopeTools(scope)
    agent._tools = facade.wrap_tools(agent.tools)
    return agent, scope, facade, judge


@pytest.mark.asyncio
async def test_wrapped_tool_parameter_alias_repair():
    agent, scope, facade, _judge = setup_agent()
    request = scope.begin_request("Open Notepad", source_id="voice:1")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)
    ctx = SimpleNamespace(speech_handle=speech)

    # Calling open_app with alias name='Notepad' instead of app_name='Notepad'
    tool = next(t for t in agent.tools if t.info.name == "open_app")
    result = await tool(ctx, name="Notepad")

    assert result == "Opened Notepad"
    assert agent.opened_apps == ["Notepad"]


@pytest.mark.asyncio
async def test_wrapped_tool_spotify_alias_repair():
    agent, scope, facade, _judge = setup_agent()
    request = scope.begin_request("Play My Phonk Songs playlist", source_id="voice:1")
    speech = SpeechHandle.create()
    facade.bind_speech(speech, request)
    ctx = SimpleNamespace(speech_handle=speech)

    # Calling spotify_play_playlist with alias playlist='My Phonk Songs' instead of name='My Phonk Songs'
    tool = next(t for t in agent.tools if t.info.name == "spotify_play_playlist")
    result = await tool(ctx, playlist="My Phonk Songs")

    assert result == "Playing My Phonk Songs"
    assert agent.played_playlists == ["My Phonk Songs"]


@pytest.mark.asyncio
async def test_unmapped_speech_handle_cannot_adopt_the_latest_request():
    agent, scope, _facade, _judge = setup_agent()
    # Begin request on scope, but DO NOT call facade.bind_speech
    scope.begin_request("Open Notepad", source_id="voice:1")
    unmapped_speech = SpeechHandle.create()
    ctx = SimpleNamespace(speech_handle=unmapped_speech)

    tool = next(t for t in agent.tools if t.info.name == "open_app")
    with pytest.raises(ToolError):
        await tool(ctx, app_name="Notepad")
    assert agent.opened_apps == []


@pytest.mark.asyncio
async def test_affirmation_does_not_inherit_an_unasked_previous_action():
    scope = RequestScope(MockJudge())
    r1 = scope.begin_request("Play My Phonk Songs playlist", source_id="voice:1")
    assert r1.text == "Play My Phonk Songs playlist"

    # User says 'yes' in subsequent turn
    r2 = scope.begin_request("yes", source_id="voice:2")
    assert r2.text == "yes"
    assert r2.answers == ()
    assert r2.id != r1.id


@pytest.mark.asyncio
async def test_local_reads_bypass_judge():
    agent, _scope, _facade, judge = setup_agent()
    # Even without begin_request or speech_handle, safe local reads execute immediately
    view_tool = next(t for t in agent.tools if t.info.name == "view_skill")
    result = await view_tool(None, name="sample_skill")
    assert result == "# Skill: sample_skill"

    list_wf = next(t for t in agent.tools if t.info.name == "list_workflows")
    wf_res = await list_wf(None)
    assert wf_res == "workflow-1, workflow-2"

    # Verify no policy judge calls were made for safe reads
    assert len(judge.calls) == 0
