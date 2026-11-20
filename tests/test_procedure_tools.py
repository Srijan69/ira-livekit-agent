import json

import pytest
from livekit.agents import ToolError
from test_task_routing import RESEARCH

from agent import Assistant
from skill_store import SkillStore
from skills import SkillCatalog


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "offline-test-key")


def assistant_for(tmp_path):
    catalog = SkillCatalog(tmp_path / "procedures")
    catalog.create_skill("research-topic", RESEARCH, category="web")
    return Assistant(
        skills=SkillStore(tmp_path / "compiled"), skills_catalog=catalog
    ), catalog


async def test_procedure_loading_requires_current_request_and_truthful_record(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    with pytest.raises(ToolError, match="request"):
        await assistant.run_skill(None, "research-topic")
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    response = json.loads(await assistant.run_skill(None, "Research topic"))
    assert response["status"] == "procedure_ready"
    assert response["run_id"]
    assert "inputs" not in response
    assert response["request_id"] == request.id
    record = catalog.state_store.run(response["run_id"])
    assert record["status"] == "loaded" and not record["verified"]
    assert (
        catalog.state_store.state(catalog.get_skill("research-topic"))["use_count"] == 0
    )
    assert json.loads(await assistant.skill_status(None))["status"] == "loaded"
    await assistant.record_procedure_action(
        request.id, "read_page", "observed", "Microsoft observed", action_id="read-one"
    )
    assert catalog.state_store.run(response["run_id"])["status"] == "running"
    finished = await assistant.finish_procedure(
        request.id,
        True,
        "Microsoft summary",
        criteria={"read_sources": True, "summary_with_sources": True},
    )
    assert finished["status"] == "done" and finished["verified"]


async def test_wrong_current_task_cannot_load_unrelated_procedure(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    catalog.create_skill("open-calculator", "Open Calculator", category="desktop")
    assistant._request_scope.begin_request(
        "Do a deep search about Microsoft", source_id="human"
    )
    with pytest.raises(ToolError, match=r"fit|metadata"):
        await assistant.run_skill(None, "open-calculator")
    assert catalog.state_store.run_store.list() == []


async def test_task_supersession_cannot_verify_old_loaded_run(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    response = json.loads(await assistant.run_skill(None, "research-topic"))
    assistant._request_scope.begin_request("Open Notepad", source_id="new-human")
    with pytest.raises(ToolError, match=r"current|task"):
        await assistant.record_procedure_action(
            request.id, "read_page", "observed", "old evidence"
        )
    assert catalog.state_store.run(response["run_id"])["status"] == "loaded"


async def test_primary_procedure_enable_and_archive_names_resolve_internally(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    enabled = await assistant.enable_skill(None, "Research topic")
    assert "Enabled" in enabled
    assert (
        catalog.state_store.state(catalog.get_skill("research-topic"))["state"]
        == "enabled"
    )
    assert (
        "archived"
        in (await assistant.delete_skill(None, "procedure:web/research-topic")).lower()
    )
    assert catalog.get_skill("research-topic") is None


async def test_repeated_same_request_load_reuses_existing_run(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    first = json.loads(await assistant.run_skill(None, "research-topic"))
    second = json.loads(await assistant.run_skill(None, "research-topic"))
    assert first["run_id"] == second["run_id"]
    assert len(catalog.state_store.run_store.list()) == 1


async def test_changed_plan_settles_previous_run_before_new_mapping(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    first = json.loads(await assistant.run_skill(None, "research-topic"))
    catalog.edit_skill("research-topic", RESEARCH + "\nUpdated procedure.")
    second = json.loads(await assistant.run_skill(None, "research-topic"))
    assert first["run_id"] != second["run_id"]
    assert catalog.state_store.run(first["run_id"])["status"] == "cancelled"
    assert catalog.state_store.run(second["run_id"])["status"] == "loaded"
    assert assistant._procedure_run_for(request.id) == second["run_id"]


@pytest.mark.parametrize("change", ["support", "archive"])
async def test_sync_active_guard_replans_same_task_after_package_changes(
    tmp_path, change
):
    assistant, catalog = assistant_for(tmp_path)
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    loaded = json.loads(await assistant.run_skill(None, "research-topic"))
    if change == "support":
        catalog.write_supporting_file(
            "research-topic", "references/source.md", "Changed source guidance"
        )
        catalog.validate_skill(
            "research-topic", available_tools=("browse_to", "read_page")
        )
    else:
        catalog.archive_skill("research-topic")
    with pytest.raises(ToolError) as error:
        assistant.validate_active_procedure(request.id, "read_page")
    result = json.loads(str(error.value))
    assert result["status"] == "procedure_replan_required" and result["retryable"]
    assert catalog.state_store.run(loaded["run_id"])["status"] in {
        "cancelled",
        "uncertain",
    }
    assert assistant._request_scope.require(request.id).id == request.id


async def test_trusted_completion_after_normal_request_closure_only_settles_history(
    tmp_path,
):
    assistant, catalog = assistant_for(tmp_path)
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    loaded = json.loads(await assistant.run_skill(None, "research-topic"))
    await assistant.record_procedure_action(
        request.id, "read_page", "observed", "Microsoft observed"
    )
    assistant._request_scope.finish(request.id)
    result = await assistant.finish_procedure(
        request.id,
        True,
        "Summary",
        criteria={"read_sources": True, "summary_with_sources": True},
    )
    assert result["status"] == "done" and result["verified"]
    assert catalog.state_store.run(loaded["run_id"])["status"] == "done"
    with pytest.raises(ToolError):
        assistant.validate_active_procedure(request.id, "read_page")


async def test_superseded_completion_cannot_verify_previous_run(tmp_path):
    assistant, catalog = assistant_for(tmp_path)
    request = assistant._request_scope.begin_request(
        "Run research topic about Microsoft", source_id="human"
    )
    loaded = json.loads(await assistant.run_skill(None, "research-topic"))
    await assistant.record_procedure_action(
        request.id, "read_page", "observed", "Microsoft observed"
    )
    newer = assistant._request_scope.begin_request(
        "Open Notepad", source_id="new-human"
    )
    result = await assistant.finish_procedure(
        request.id,
        True,
        "Late summary",
        criteria={"read_sources": True, "summary_with_sources": True},
    )
    assert result["status"] in {"cancelled", "uncertain"} and not result["verified"]
    assert catalog.state_store.run(loaded["run_id"])["verified"] is False
    assert assistant._request_scope.require(newer.id).id == newer.id
