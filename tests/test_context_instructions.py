"""Offline budget and prompt-contract checks; these are not live model evals."""

import json
import re
from types import SimpleNamespace

import pytest
from livekit.agents import llm
from livekit.plugins.google.realtime.realtime_api import create_tools_config

from context_instructions import context_instructions
from context_summary import estimate_tokens


def test_compact_prompt_leaves_capacity_for_profile_and_structured_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-prompt-review-key")
    from agent import Assistant
    from dual_memory import DualMemoryStore
    from personal_memory import PersonalMemoryStore
    from tasks import TaskStore
    from tool_mastery import ToolMasteryStore

    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assistant = Assistant(
            dual_memory=DualMemoryStore(tmp_path / "legacy"),
            personal_memory=store,
            skills_catalog=SimpleNamespace(
                root=tmp_path / "skills", render_prompt_catalog=lambda: ""
            ),
            tool_mastery=ToolMasteryStore(tmp_path / "mastery.sqlite3"),
            tasks=TaskStore(":memory:"),
        )
        tools, _ = create_tools_config(
            llm.ToolContext(assistant.tools), use_parameters_json_schema=False
        )
        tool_tokens = estimate_tokens(
            [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        )
        profile_tokens = estimate_tokens(
            json.dumps(
                {
                    "name": "n" * 120,
                    "age": 130,
                    "occupation": "o" * 200,
                    "preferences": "p" * 4000,
                }
            )
        )
        # Full canonical fields, maximum summary, a small recent tail, the
        # integration's background label and the required output/audio reserve.
        assert (
            estimate_tokens(context_instructions())
            + tool_tokens
            + profile_tokens
            + 2000
            + 200
            + 200
            + 2048
            <= 16000
        )
        assert len(context_instructions()) <= 8000
    finally:
        store.close()


@pytest.mark.parametrize(
    "required",
    [
        r"female.*woman.*voice",
        r"name does not stand for anything",
        r"canonical profile.*authoritative.*blank.*absent",
        r"current trusted human request.*authoriz",
        r"recalled.*never.*permission",
        r"inspection.only.*observation only",
        r"clarify_request.*actual answer",
        r"finish_request.*observed.*goal",
        r"numbering.*changes.*list again",
        r"observation_id.*latest.*image.*pixel",
        r"waiting.*run_id.*question_id.*answer_skill",
        r"draft.*enable_skill.*explicit",
        r"not.*verified.*not.*completed",
        r"current_time.*relative dates.*timezone",
        r"to.do.*not.*alarm",
    ],
)
def test_critical_instruction_contracts_survive_compaction(required):
    # These guards detect omitted policy while editing the shorter prompt.
    # Runtime action enforcement remains covered by request-scope/SDK tests.
    assert re.search(required, " ".join(context_instructions().split()), re.IGNORECASE)


def test_compact_prompt_has_no_personal_examples_or_editorial_placeholders():
    prompt = context_instructions()
    assert not re.search(r"\b\d{8,}\b|https?://|TODO|PLACEHOLDER", prompt)
    assert not any(
        name in prompt.casefold() for name in ("friday", "stark", "iron man")
    )


async def test_real_assistant_prioritizes_full_profile_and_summary_over_dense_notes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-memory-composition-key")
    from agent import Assistant
    from dual_memory import DualMemoryStore
    from personal_memory import PersonalMemoryStore
    from session_memory import SessionMemoryIntegration
    from tasks import TaskStore
    from tool_mastery import ToolMasteryStore

    store = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    legacy = DualMemoryStore(tmp_path / "legacy")
    profile = {
        "name": "n" * 120,
        "age": 130,
        "occupation": "o" * 200,
        "preferences": "p" * 3974 + " complete preference end",
    }
    store.save_profile(profile)
    for number in range(10):
        store.add_fact(
            f"Workshop archived detail {number}: " + "a" * 3000, target="user"
        )
        store.add_fact(
            f"Garden archived detail {number}: " + "b" * 3000, target="memory"
        )
    cid = store.start_conversation("recovery")
    store.append_item(
        cid,
        "source",
        "user",
        "Garden project constraints and unresolved source details",
        trusted=True,
    )

    def entry(text):
        return {"content": text, "source_item_ids": ["source"]}

    checkpoint = {
        "overview": "Garden project. " + "v" * 950,
        "facts": [],
        "decisions": [
            entry("Keep design constraint " + "d" * 1400),
            entry("Keep release constraint " + "e" * 1400),
        ],
        "open_threads": [entry("Still verify source detail " + "x" * 900)],
        "outcomes": [],
        "questions": [
            entry("Unresolved question. " + "q" * 300 + " SUMMARY END PRESERVED")
        ],
    }
    store.save_checkpoint(
        cid,
        through_seq=1,
        summary=checkpoint,
        expected_memory_revision=store.memory_revision(),
        source_item_ids=["source"],
    )
    for number in range(10):
        store.append_item(
            cid,
            f"recent-{number}",
            "user",
            "Keep the latest project discussion",
            trusted=True,
        )
    assistant = Assistant(
        dual_memory=legacy,
        personal_memory=store,
        skills_catalog=SimpleNamespace(
            root=tmp_path / "skills", render_prompt_catalog=lambda: ""
        ),
        tool_mastery=ToolMasteryStore(tmp_path / "mastery.sqlite3"),
        tasks=TaskStore(":memory:"),
    )
    runtime = SessionMemoryIntegration(assistant, store, assistant._dual_memory)
    try:
        await runtime.prepare()
        for value in profile.values():
            assert str(value) in assistant.instructions
        assert "SUMMARY END PRESERVED" in assistant.instructions
        assert store.search("Workshop archived detail 0")
        tools, _ = create_tools_config(
            llm.ToolContext(assistant.tools), use_parameters_json_schema=False
        )
        tool_tokens = estimate_tokens(
            [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        )
        turns, _ = assistant.chat_ctx.to_provider_format(
            format="google", inject_dummy_user_message=False
        )
        assert (
            estimate_tokens(assistant.instructions)
            + tool_tokens
            + estimate_tokens(turns)
            <= 16000 - 2048
        )
    finally:
        # No provider client was created by prepare; close the empty summarizer
        # directly rather than starting a shutdown summary network request.
        await runtime.summarizer.aclose()
        store.close()
