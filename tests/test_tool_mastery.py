"""Tests for Tool Mastery & Adaptive Training Engine in IRA."""

import pytest

from agent import Assistant
from tool_mastery import (
    ToolMasteryStore,
    ToolProfile,
    ToolTrainer,
)


@pytest.fixture
def tmp_store(tmp_path):
    store_file = tmp_path / ".tool_mastery.json"
    return ToolMasteryStore(store_file)


def test_tool_profile_level_progression():
    profile = ToolProfile(name="test_tool")
    assert profile.calculate_level() == "Novice"

    profile.consecutive_successes = 3
    assert profile.calculate_level() == "Practiced"

    profile.consecutive_successes = 0
    profile.success_count = 5
    assert profile.calculate_level() == "Practiced"

    profile.consecutive_successes = 5
    assert profile.calculate_level() == "Mastered"

    profile.consecutive_successes = 0
    profile.success_count = 15
    assert profile.calculate_level() == "Mastered"


def test_tool_mastery_store_record_success(tmp_store):
    profile = tmp_store.record_success("open_app")
    assert profile.use_count == 1
    assert profile.success_count == 1
    assert profile.consecutive_successes == 1
    assert profile.level == "Novice"
    assert profile.last_used_at is not None

    for _ in range(4):
        profile = tmp_store.record_success("open_app")
    assert profile.consecutive_successes == 5
    assert profile.level == "Mastered"

    # Verify persistence
    reloaded = ToolMasteryStore(tmp_store.path)
    loaded_profile = reloaded.get_profile("open_app")
    assert loaded_profile.success_count == 5
    assert loaded_profile.level == "Mastered"


def test_tool_mastery_store_record_failure_and_adaptation(tmp_store):
    # Record timeout failure
    profile = tmp_store.record_failure("browse_to", "Page load timed out after 10s")
    assert profile.failure_count == 1
    assert profile.consecutive_successes == 0
    assert profile.failure_signatures.get("timeout") == 1
    assert profile.calibrated_params.get("timeout_seconds") == 12.5

    # Record element resolution failure
    profile = tmp_store.record_failure(
        "click_element", "No such element with selector #submit"
    )
    assert profile.failure_signatures.get("element_resolution") == 1
    assert profile.calibrated_params.get("scroll_before_interact") is True
    assert profile.calibrated_params.get("element_debounce_ms") == 350

    # Record coordinate drift failure
    profile = tmp_store.record_failure(
        "click_mouse", "Target coordinates outside screen bounds"
    )
    assert profile.failure_signatures.get("coordinate_drift") == 1
    assert profile.calibrated_params.get("require_fresh_capture") is True

    # Record app launch failure
    profile = tmp_store.record_failure(
        "open_app", "Start menu did not open app within timeout"
    )
    assert profile.failure_signatures.get("launch_delay") == 1
    assert profile.calibrated_params.get("start_menu_delay") == 0.9


def test_tool_mastery_catalog(tmp_store):
    tmp_store.record_success("open_app")
    tmp_store.record_failure("open_app", "generic failure")
    tmp_store.record_success("browse_to")

    catalog = tmp_store.catalog()
    assert len(catalog) == 2
    tools = {item["tool"]: item for item in catalog}
    assert tools["open_app"]["use_count"] == 2
    assert tools["open_app"]["success_rate"] == "50.0%"
    assert tools["browse_to"]["use_count"] == 1
    assert tools["browse_to"]["success_rate"] == "100.0%"


def test_tool_trainer_drills(tmp_store):
    trainer = ToolTrainer(tmp_store)

    # Train open_app
    res_app = trainer.train_tool("open_app")
    assert res_app["success"] is True
    assert res_app["drills_passed"] == 3
    assert res_app["calibrated_params"]["sanitization_verified"] is True
    assert tmp_store.get_profile("open_app").level == "Practiced"

    # Train browse_to
    res_browse = trainer.train_tool("browse_to")
    assert res_browse["success"] is True
    assert res_browse["drills_passed"] == 3
    assert res_browse["calibrated_params"]["url_safety_verified"] is True

    # Train click_mouse
    res_mouse = trainer.train_tool("click_mouse")
    assert res_mouse["success"] is True
    assert res_mouse["drills_passed"] == 3
    assert res_mouse["calibrated_params"]["geometry_verified"] is True

    # Train memory
    res_mem = trainer.train_tool("memory")
    assert res_mem["success"] is True
    assert res_mem["drills_passed"] == 3
    assert res_mem["calibrated_params"]["typography_folding_verified"] is True

    # Train generic tool
    res_generic = trainer.train_tool("custom_action")
    assert res_generic["success"] is True
    assert res_generic["drills_passed"] == 2
    assert res_generic["calibrated_params"]["generic_verified"] is True

    # Train invalid identifier
    res_invalid = trainer.train_tool("bad name with spaces!")
    assert res_invalid["success"] is False


@pytest.mark.asyncio
async def test_assistant_tool_mastery_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")
    store = ToolMasteryStore(tmp_path / ".tool_mastery.json")
    assistant = Assistant(tool_mastery=store)

    # Train a tool via assistant
    result = await assistant.train_tool(None, "open_app")
    assert "Training complete, sir." in result
    assert "open_app" in result

    # Check status of specific tool
    status = await assistant.tool_mastery_status(None, "open_app")
    assert "open_app" in status
    assert "Level Practiced" in status

    # Check catalog status
    catalog_status = await assistant.tool_mastery_status(None, "")
    assert "Tool mastery ledger:" in catalog_status
    assert "open_app" in catalog_status
