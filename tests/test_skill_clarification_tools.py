"""Voice tools keep a waiting skill addressable without replaying its actions."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from livekit.agents import ToolError

from agent import Assistant
from skill_format import (
    Action,
    Check,
    Skill,
    SkillInput,
    Step,
    parse_skill,
    render_skill,
)
from skill_store import SkillStore


@pytest.fixture(autouse=True)
def model_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")


def asking_skill():
    return Skill(
        name="Choose a page",
        description="Ask for a page then verify it.",
        state="enabled",
        allowed_tools=("browse_to",),
        inputs=(SkillInput("url", "A page address"),),
        steps=(
            Step(1, "Ask", action=Action("ask", param="url", text="Which page?")),
            Step(
                2,
                "Open",
                action=Action("tool", tool="browse_to", args=(("url", "{url}"),)),
                check=Check("page_contains", "Example"),
            ),
        ),
    )


def assistant_for(tmp_path):
    store = SkillStore(tmp_path)
    store.save(asking_skill(), source="compiled")
    actions, events = [], []
    assistant = Assistant(
        skills=store,
        browser=SimpleNamespace(navigate=actions.append, observe=lambda *_: True),
        publish_event=events.append,
    )
    return assistant, store, actions, events


async def test_waiting_tool_returns_question_then_answer_completes_same_run(tmp_path):
    assistant, store, actions, events = assistant_for(tmp_path)
    try:
        response = json.loads(await assistant.run_skill(None, "choose-a-page"))
        assert response["status"] == "waiting"
        assert response["question"] == "Which page?"
        assert response["question_id"]
        assert actions == []
        assert store.catalog()[0]["use_count"] == 0
        assert {"type": "skills.changed"} in events
        status = json.loads(await assistant.skill_status(None))
        assert status["question_id"] == response["question_id"]
        answer = await assistant.answer_skill(
            None,
            answer="https://example.com",
            run_id=response["run_id"],
            question_id=response["question_id"],
        )
        assert "completed" in answer.lower()
        assert actions == ["https://example.com"]
        assert store.catalog()[0]["use_count"] == 1
        history = assistant._skill_service.run_store.list()
        assert len(history) == 1
        assert history[0]["run_id"] == response["run_id"]
        assert history[0]["verified"] is True
    finally:
        if assistant._skill_service:
            await assistant._skill_service.aclose()


async def test_invalid_or_stale_voice_answer_keeps_question_and_has_no_effect(tmp_path):
    assistant, store, actions, _ = assistant_for(tmp_path)
    try:
        response = json.loads(await assistant.run_skill(None, "choose-a-page"))
        for answer, question_id in [
            ("javascript:secret()", response["question_id"]),
            ("https://example.com", "old-question"),
        ]:
            with pytest.raises(ToolError):
                await assistant.answer_skill(
                    None,
                    answer=answer,
                    run_id=response["run_id"],
                    question_id=question_id,
                )
        assert actions == []
        assert store.catalog()[0]["use_count"] == 0
        status = json.loads(await assistant.skill_status(None))
        assert status["status"] == "waiting"
        assert status["question_id"] == response["question_id"]
    finally:
        if assistant._skill_service:
            await assistant._skill_service.aclose()


async def test_waiting_stop_is_accounted_and_cannot_later_resume(tmp_path):
    assistant, store, actions, _ = assistant_for(tmp_path)
    try:
        response = json.loads(await assistant.run_skill(None, "choose-a-page"))
        await assistant.stop_skill(None, response["run_id"])
        for _ in range(100):
            status = json.loads(await assistant.skill_status(None))
            if status["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        assert status["status"] == "cancelled"
        assert store.catalog()[0]["failure_count"] == 1
        with pytest.raises(ToolError):
            await assistant.answer_skill(
                None,
                answer="https://example.com",
                run_id=response["run_id"],
                question_id=response["question_id"],
            )
        assert actions == []
    finally:
        if assistant._skill_service:
            await assistant._skill_service.aclose()


def test_new_observations_round_trip_in_user_editable_skill():
    markdown = """---
name: Verify field
description: Observe an editable field.
---
## Steps
1. **Window**
   - do: check active_window "IRA Fixture"
2. **Value**
   - do: check page_value "{\\"selector\\":\\"#name\\",\\"value\\":\\"Ada\\"}"
"""
    skill = parse_skill(markdown)
    assert skill.steps[0].action.check == Check("active_window", "IRA Fixture")
    assert json.loads(skill.steps[1].action.check.value) == {
        "selector": "#name",
        "value": "Ada",
    }
    assert parse_skill(render_skill(skill)) == skill


@pytest.mark.parametrize("question", ["Continue?", "{url}"])
def test_single_token_question_round_trips_for_saving(question):
    skill = asking_skill()
    ask = replace(skill.steps[0], action=replace(skill.steps[0].action, text=question))
    original = replace(skill, steps=(ask, skill.steps[1]))
    assert parse_skill(render_skill(original)) == original


@pytest.mark.parametrize(
    "question", ["अ " * 400 + "?", '"' * 950 + "?"], ids=["unicode", "escaped"]
)
async def test_long_waiting_question_keeps_valid_json_and_exact_ids(tmp_path, question):
    assistant, store, _, _ = assistant_for(tmp_path)
    skill = asking_skill()
    ask = replace(skill.steps[0], action=replace(skill.steps[0].action, text=question))
    store.save(replace(skill, steps=(ask, skill.steps[1])))
    try:
        response = await assistant.run_skill(None, "choose-a-page")
        assert len(response) <= 2000
        waiting = json.loads(response)
        status_text = await assistant.skill_status(None)
        assert len(status_text) <= 2000
        status = json.loads(status_text)
        assert status["question_id"] == waiting["question_id"]
        assert status["run_id"] == waiting["run_id"]
        assert status["status"] == "waiting"
    finally:
        if assistant._skill_service:
            await assistant._skill_service.aclose()
        if assistant._skill_monitors:
            await asyncio.gather(*assistant._skill_monitors.values())
