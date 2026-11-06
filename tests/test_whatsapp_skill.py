"""Tests verifying WhatsApp skill validation, tool adapters, and execution."""

from unittest.mock import MagicMock

import pytest

from agent import Assistant
from skill_adapters import SkillToolAdapters
from skills import SkillCatalog


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


def test_whatsapp_skill_parses_and_validates():
    catalog = SkillCatalog()
    skill = catalog.get_skill("send-whatsapp-message")
    assert skill is not None, "send-whatsapp-message skill must exist"
    assert skill.category == "communication"
    assert "windows" in skill.platforms
    view = catalog.view_skill("send-whatsapp-message")
    assert view["success"] is True
    assert "web.whatsapp.com" in view["instructions"]


def test_skill_adapters_support_desktop_actions():
    desktop = MagicMock()
    adapters = SkillToolAdapters(MagicMock(), desktop, MagicMock())
    reg = adapters.registry()

    assert "type_on_desktop" in reg.names()
    assert "click_desktop_element" in reg.names()
    assert "click_mouse" in reg.names()
    assert "move_mouse" in reg.names()

    # Test type_on_desktop
    adapters.type_on_desktop("hello", window_title="Notes")
    desktop.type_in_window.assert_called_with("hello", "Notes")

    # Test type_on_desktop without window_title
    adapters.type_on_desktop("hello")
    desktop.type_text.assert_called_with("hello")

    # Test click_desktop_element
    adapters.click_desktop_element("Submit", window_title="Notes", x=50, y=50)
    desktop.click_in_window.assert_called_with(50, 50, "Notes", button="left")

    # Test mouse operations
    adapters.click_mouse(100, 200, button="left", clicks=1)
    desktop.click.assert_called_with(100, 200, button="left", clicks=1)

    # Test move_mouse
    adapters.move_mouse(150, 250)
    desktop.move.assert_called_with(150, 250)


@pytest.mark.asyncio
async def test_whatsapp_skill_executes_in_assistant():
    catalog = SkillCatalog()
    assistant = Assistant(skills_catalog=catalog)
    view = await assistant.skill_view(None, name="send-whatsapp-message")
    assert "web.whatsapp.com" in view
    assert "send-whatsapp-message" in view
