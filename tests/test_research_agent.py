"""Research evidence is collected by actual exported Assistant tools."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from dual_memory import DualMemoryStore
from request_scope import AdmissionDecision, RequestScope
from skill_store import SkillStore
from skills import SkillCatalog
from tasks import TaskStore


@pytest.fixture
async def research_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    browser = SimpleNamespace(
        current_url="https://www.microsoft.com/about",
        read_page=lambda: "Microsoft company overview",
    )

    async def policy(*_):
        return AdmissionDecision("allow", "Inert test policy")

    scope = RequestScope(policy)
    assistant = Assistant(
        browser=browser,
        request_scope=scope,
        dual_memory=DualMemoryStore(tmp_path / "memory"),
        skills=SkillStore(tmp_path / "workflows"),
        skills_catalog=SkillCatalog(tmp_path / "skills"),
        tasks=TaskStore(":memory:"),
    )
    request = scope.begin_request(
        "Do a deep search about Microsoft", source_id="typed:research"
    )
    speech = SpeechHandle.create()
    assistant._scoped_tools.bind_speech(speech, request)
    context = SimpleNamespace(
        speech_handle=speech, function_call=SimpleNamespace(call_id="research-call")
    )
    tools = {tool.info.name: tool for tool in assistant.tools}
    return assistant, browser, scope, context, tools


async def test_exported_page_reads_supply_research_status(research_agent):
    assistant, browser, _, context, tools = research_agent
    try:
        for url in (
            "https://www.microsoft.com/about",
            "https://www.microsoft.com/investor",
            "https://www.reuters.com/technology/microsoft",
        ):
            browser.current_url = url
            await tools["read_page"](context)
        status = json.loads(await tools["research_status"](context))
        assert status["ready"] is True
        assert len(status["sources"]) == 3
    finally:
        await assistant._scoped_tools.aclose()


async def test_research_cannot_finish_before_sources_and_summary(research_agent):
    assistant, _, _, context, tools = research_agent
    try:
        await tools["read_page"](context)
        with pytest.raises(ToolError):
            await tools["finish_request"](context)
    finally:
        await assistant._scoped_tools.aclose()


async def test_old_sources_cannot_complete_a_new_research_task(research_agent):
    assistant, _, scope, context, tools = research_agent
    try:
        await tools["read_page"](context)
        request = scope.begin_request("Search about Google", source_id="typed:google")
        assistant._scoped_tools.bind_speech(context.speech_handle, request)
        status = json.loads(await tools["research_status"](context))
        assert status["subject"] == "Google"
        assert status["sources"] == []
    finally:
        await assistant._scoped_tools.aclose()


async def test_research_discovery_omits_unrelated_app_procedures(research_agent):
    assistant, _, _, context, tools = research_agent
    assistant._skills_catalog.create_skill(
        "open-notes",
        "---\nname: open-notes\nmetadata:\n  ira:\n    task_kinds: [open_app]\n---\nOpen Notes.",
    )
    assistant._skills_catalog.create_skill(
        "research",
        "---\nname: research\nmetadata:\n  ira:\n    task_kinds: [web_research]\n---\nResearch the topic.",
    )
    try:
        result = await tools["skills_list"](context)
        assert "research" in result
        assert "open-notes" not in result
    finally:
        await assistant._scoped_tools.aclose()


async def test_stopping_task_cancels_adaptive_procedure_history(research_agent):
    from test_task_routing import RESEARCH

    assistant, _, scope, context, tools = research_agent
    assistant._skills_catalog.create_skill("research-topic", RESEARCH, category="web")
    request = scope.begin_request(
        "Run research topic about Microsoft", source_id="typed:explicit"
    )
    assistant._scoped_tools.bind_speech(context.speech_handle, request)
    try:
        response = json.loads(await tools["run_skill"](context, name="research-topic"))
        await tools["read_page"](context)
        await tools["stop_actions"](context)
        await asyncio.sleep(0)
        jobs = tuple(getattr(assistant, "_procedure_cleanup_jobs", ()))
        if jobs:
            await asyncio.gather(*jobs)
        record = assistant._skills_catalog.state_store.run(response["run_id"])
        assert record["status"] == "cancelled"
        assert record["verified"] is False
    finally:
        await assistant._scoped_tools.aclose()


async def test_research_completion_records_verified_procedure_criteria(research_agent):
    from test_task_routing import RESEARCH

    assistant, browser, scope, context, tools = research_agent
    assistant._skills_catalog.create_skill("research-topic", RESEARCH, category="web")
    request = scope.begin_request(
        "Run research topic about Microsoft with deep research",
        source_id="typed:complete",
    )
    assistant._scoped_tools.bind_speech(context.speech_handle, request)
    try:
        response = json.loads(await tools["run_skill"](context, name="research-topic"))
        for url in (
            "https://www.microsoft.com/about",
            "https://www.microsoft.com/investor",
            "https://www.reuters.com/technology/microsoft",
        ):
            browser.current_url = url
            await tools["read_page"](context)
        await tools["finish_request"](
            context,
            summary="Microsoft overview and results, from microsoft.com and reuters.com.",
        )
        await assistant._scoped_tools.aclose()
        record = assistant._skills_catalog.state_store.run(response["run_id"])
        assert record["status"] == "done"
        assert record["verified"] is True
    finally:
        await assistant._scoped_tools.aclose()


async def test_realistic_bundled_research_steps_verify_after_navigation(research_agent):
    from test_task_routing import RESEARCH

    assistant, browser, scope, context, tools = research_agent
    metadata = RESEARCH.replace(
        "[browse_to, read_page]", "[browse_to, read_page, research_status]"
    )
    assistant._skills_catalog.create_skill("research-topic", metadata, category="web")
    request = scope.begin_request(
        "Run research topic about Microsoft with deep research",
        source_id="typed:full-flow",
    )
    assistant._scoped_tools.bind_speech(context.speech_handle, request)
    browser.navigate = lambda url: (
        setattr(browser, "current_url", url) or "Microsoft page loaded"
    )
    try:
        response = json.loads(await tools["run_skill"](context, name="research-topic"))
        for url in (
            "https://www.microsoft.com/about",
            "https://www.microsoft.com/investor",
            "https://www.reuters.com/technology/microsoft",
        ):
            await tools["browse_to"](context, url=url)
            await tools["read_page"](context)
        await tools["research_status"](context)
        await tools["finish_request"](
            context, summary="Microsoft overview from microsoft.com and reuters.com."
        )
        await assistant._scoped_tools.aclose()
        record = assistant._skills_catalog.state_store.run(response["run_id"])
        assert record["verified"] is True
    finally:
        await assistant._scoped_tools.aclose()


async def test_procedure_edit_prevents_an_exported_action_using_stale_plan(
    research_agent,
):
    from test_task_routing import RESEARCH

    assistant, _, scope, context, tools = research_agent
    assistant._skills_catalog.create_skill("research-topic", RESEARCH, category="web")
    request = scope.begin_request(
        "Run research topic about Microsoft", source_id="typed:stale"
    )
    assistant._scoped_tools.bind_speech(context.speech_handle, request)
    try:
        response = json.loads(await tools["run_skill"](context, name="research-topic"))
        assistant._skills_catalog.edit_skill(
            "research-topic", RESEARCH + "\nChanged method."
        )
        with pytest.raises(ToolError):
            await tools["read_page"](context)
        record = assistant._skills_catalog.state_store.run(response["run_id"])
        assert record["status"] in {"cancelled", "uncertain"}
        assert record["verified"] is False
    finally:
        await assistant._scoped_tools.aclose()


async def test_new_final_voice_input_retires_old_adaptive_run(research_agent):
    from livekit.agents.voice.events import UserInputTranscribedEvent
    from test_task_routing import RESEARCH

    assistant, _, scope, context, tools = research_agent
    assistant._skills_catalog.create_skill("research-topic", RESEARCH, category="web")
    request = scope.begin_request(
        "Run research topic about Microsoft", source_id="typed:previous"
    )
    assistant._scoped_tools.bind_speech(context.speech_handle, request)
    try:
        response = json.loads(await tools["run_skill"](context, name="research-topic"))
        await tools["read_page"](context)
        assistant._scoped_tools._on_transcript(
            UserInputTranscribedEvent(
                transcript="Open Notepad", is_final=True, item_id="new-final-input"
            )
        )
        await assistant._scoped_tools.aclose()
        assert (
            assistant._skills_catalog.state_store.run(response["run_id"])["status"]
            == "cancelled"
        )
    finally:
        await assistant._scoped_tools.aclose()
