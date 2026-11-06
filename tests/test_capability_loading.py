from unittest.mock import AsyncMock

import pytest

from agent import Assistant
from skill_tools import CAPABILITIES


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


async def test_gemini_capabilities_are_ready_before_session_and_loading_never_restarts():
    assistant = Assistant()
    original = assistant.instructions
    names = {tool.info.name for tool in assistant.tools}
    assert set().union(*CAPABILITIES.values()) <= names
    assert len(names) == len(assistant.tools)
    assert all(not model.capabilities.mutable_tools for model in assistant.llm.models)
    assistant.update_tools = AsyncMock(
        side_effect=AssertionError("Provider would reconnect")
    )
    assert {"run_skill", "stop_skill", "list_skills", "open_app", "browse_to"} <= names
    await assistant.load_capabilities(None, "browser")
    names = {tool.info.name for tool in assistant.tools}
    assert {"read_page", "click_element", "list_page_elements"} <= names
    count = len(names)
    await assistant.load_capabilities(None, "browser")
    assert len(assistant.tools) == count
    assert assistant.instructions == original
    assistant.update_tools.assert_not_called()


async def test_management_capability_is_available_without_provider_schema_mutation():
    assistant = Assistant()
    assert "enable_skill" in {tool.info.name for tool in assistant.tools}
    await assistant.load_capabilities(None, "skills")
    assert {"enable_skill", "save_skill", "learn_skill"} <= {
        tool.info.name for tool in assistant.tools
    }


def test_google_connect_schema_includes_registered_desktop_and_browser_tools():
    from livekit.agents.llm import ToolContext
    from livekit.plugins.google.realtime.realtime_api import create_tools_config

    assistant = Assistant()
    configs, _ = create_tools_config(
        ToolContext(assistant.tools), use_parameters_json_schema=False
    )
    declared = {
        fn.name for config in configs for fn in (config.function_declarations or [])
    }
    assert {
        "move_mouse",
        "click_mouse",
        "scroll_mouse",
        "drag_mouse",
        "read_page",
        "click_element",
    } <= declared
