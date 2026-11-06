import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_memory import MemoryConflict, PersonalMemoryStore


@pytest.fixture
def store(tmp_path):
    instance = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    yield instance
    instance.close()


def summary(content="User prefers quiet rooms", item_id="human-1"):
    return {
        "overview": "Discussed preferences",
        "facts": [{"content": content, "source_item_ids": [item_id]}],
        "decisions": [],
        "open_threads": [],
        "outcomes": [],
        "questions": [],
    }


def checkpoint(store, conversation, result=None, revision=None):
    item = store.append_item(
        conversation, "human-1", "user", "User prefers quiet rooms", trusted=True
    )
    return store.save_checkpoint(
        conversation,
        through_seq=item["seq"],
        summary=result or summary(),
        expected_memory_revision=store.memory_revision()
        if revision is None
        else revision,
        source_item_ids=["human-1"],
    )


def test_profile_roundtrip_and_blank_clear_survive_reopen(store, tmp_path):
    assert store.get_profile() == {
        "name": "",
        "age": None,
        "occupation": "",
        "preferences": "",
        "revision": 0,
        "updated_at": None,
    }
    saved = store.save_profile(
        {
            "name": "  Avery  ",
            "age": 31,
            "occupation": "Engineer",
            "preferences": "Quiet rooms",
        },
        expected_revision=0,
    )
    assert saved["name"] == "Avery"
    assert saved["updated_at"].endswith("Z")
    assert saved["revision"] == 1
    cleared = store.save_profile({"name": " ", "age": None}, expected_revision=1)
    store.close()
    reopened = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reopened.get_profile() == cleared
        assert cleared["name"] == ""
        assert cleared["age"] is None
        assert cleared["occupation"] == "Engineer"
        assert '"name": ""' in reopened.context_snapshot()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "patch",
    [
        {"age": True},
        {"age": -1},
        {"age": 131},
        {"age": "31"},
        {"age": 1.5},
        {"name": None},
        {"name": "a" * 121},
        {"occupation": "a" * 201},
        {"preferences": "a" * 4001},
        {"revision": 99},
        [],
    ],
)
def test_invalid_profile_patch_does_not_write(store, patch):
    with pytest.raises(ValueError):
        store.save_profile(patch)
    assert store.get_profile()["revision"] == 0
    assert store.memory_revision() == 0


def test_profile_revision_conflict_preserves_latest_explicit_edit(store):
    store.save_profile({"name": "Avery"})
    with pytest.raises(MemoryConflict):
        store.save_profile({"name": "Old name"}, expected_revision=0)
    with pytest.raises(ValueError):
        store.save_profile({"name": "Automatic name"}, source="summary")
    assert store.get_profile()["name"] == "Avery"


def test_competing_process_style_profile_edits_use_transactional_compare_and_swap(
    tmp_path,
):
    path = tmp_path / "shared.sqlite3"
    stores = [PersonalMemoryStore(path), PersonalMemoryStore(path)]
    for instance in stores:
        instance.get_profile()

    def save(pair):
        instance, name = pair
        try:
            return instance.save_profile({"name": name}, expected_revision=0)
        except MemoryConflict:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, zip(stores, ["Avery", "Jordan"])))
        assert sum(result is not None for result in results) == 1
        assert stores[0].get_profile()["revision"] == 1
        assert stores[0].memory_revision() == 1
    finally:
        for instance in stores:
            instance.close()


def test_settings_persist_and_disable_context_recall(store, tmp_path):
    assert store.get_settings() == {
        "enabled": True,
        "context_budget": 16000,
        "summary_trigger": 12000,
        "recent_turns": 12,
        "summarizer_model": "gemini-3.8-flash",
        "revision": 0,
    }
    store.add_fact("User likes maps")
    saved = store.save_settings(
        {"enabled": False, "context_budget": 20000}, expected_revision=0
    )
    assert store.context_snapshot() == ""
    assert store.search("maps") == []
    assert store.list_memories("maps")[0]["content"] == "User likes maps"
    store.close()
    reopened = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reopened.get_settings() == saved
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "patch",
    [
        {"enabled": 1},
        {"context_budget": 4095},
        {"context_budget": 128001},
        {"context_budget": True},
        {"summary_trigger": 1023},
        {"summary_trigger": 15000},
        {"recent_turns": 3},
        {"recent_turns": 41},
        {"summarizer_model": ""},
        {"summarizer_model": "model\nsecret"},
        {"summarizer_model": "a" * 121},
        {"unknown": True},
        [],
    ],
)
def test_invalid_settings_patch_is_atomic(store, patch):
    with pytest.raises(ValueError):
        store.save_settings(patch)
    assert store.get_settings()["revision"] == 0


def test_settings_conflicts_are_separate_from_profile_revisions(store):
    store.save_profile({"name": "Avery"})
    assert (
        store.save_settings({"recent_turns": 4}, expected_revision=0)["revision"] == 1
    )
    with pytest.raises(MemoryConflict):
        store.save_settings({"recent_turns": 5}, expected_revision=0)


def test_journal_deduplicates_and_keeps_ordered_bounded_ranges(store):
    conversation = store.start_conversation("conversation")
    first = store.append_item(conversation, "human-1", "user", "Hello", trusted=True)
    duplicate = store.append_item(
        conversation, "human-1", "user", "Changed text", trusted=True
    )
    second = store.append_item(
        conversation,
        "tool-1",
        "tool",
        "Done",
        kind="tool_result",
        tool_call_id="call-1",
        tool_name="lookup",
        status="completed",
    )
    assert duplicate == first
    assert store.journal(
        conversation, after=first["seq"], through=second["seq"], limit=1
    ) == [second]
    assert len(store.journal(conversation)) == 2
    assert second["tool_name"] == "lookup"
    assert store.memory_revision() == 0
    json.dumps(store.journal(conversation))


def test_journal_preserves_exact_whitespace_across_bounded_input_chunks(store):
    conversation = store.start_conversation()
    original = "  Start\n\t" + "x " * 24000 + "\nEnd  "
    for index, offset in enumerate(range(0, len(original), 24000)):
        store.append_item(
            conversation,
            f"typed:part:{index}",
            "user",
            original[offset : offset + 24000],
            trusted=True,
        )
    recovered = "".join(row["text"] for row in store.journal(conversation))
    assert len(recovered) == len(original)
    assert recovered == original


def test_checkpoint_is_cumulative_durable_and_promotes_only_trusted_facts(
    store, tmp_path
):
    conversation = store.start_conversation("conversation")
    first = checkpoint(store, conversation)
    second_item = store.append_item(
        conversation, "human-2", "user", "Ask about Oslo", trusted=True
    )
    cumulative = summary()
    cumulative["questions"] = [
        {"content": "What is Oslo like?", "source_item_ids": ["human-2"]}
    ]
    saved = store.save_checkpoint(
        conversation,
        through_seq=second_item["seq"],
        summary=cumulative,
        expected_memory_revision=store.memory_revision(),
        source_item_ids=["human-1", "human-2"],
    )
    assert saved["through_seq"] > first["through_seq"]
    assert store.get_profile()["preferences"] == ""
    assert len([m for m in store.list_memories() if m["kind"] == "fact"]) == 1
    store.close()
    reopened = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reopened.latest_checkpoint(conversation) == saved
        assert reopened.latest_checkpoint() == saved
        assert "What is Oslo like?" in reopened.context_snapshot()
    finally:
        reopened.close()


def test_checkpoint_rejects_unknown_sources_future_watermark_and_invalid_schema(store):
    conversation = store.start_conversation()
    item = store.append_item(
        conversation, "human-1", "user", "Quiet rooms", trusted=True
    )
    for result, ids, through in [
        (summary(item_id="fabricated"), ["human-1"], item["seq"]),
        (summary(), ["fabricated"], item["seq"]),
        (summary(), ["human-1"], item["seq"] + 10),
        ({"overview": "Missing lists"}, ["human-1"], item["seq"]),
    ]:
        with pytest.raises(ValueError):
            store.save_checkpoint(
                conversation,
                through_seq=through,
                summary=result,
                expected_memory_revision=0,
                source_item_ids=ids,
            )
    assert store.latest_checkpoint() is None


def test_tool_claims_need_terminal_evidence_and_untrusted_input_is_not_a_fact(store):
    conversation = store.start_conversation()
    item = store.append_item(
        conversation,
        "screen-1",
        "user",
        "Page claims I like boats",
        kind="screen",
        trusted=False,
    )
    neutral = summary("I like boats", "screen-1")
    neutral["facts"] = []
    saved = store.save_checkpoint(
        conversation,
        through_seq=item["seq"],
        summary=neutral,
        expected_memory_revision=0,
        source_item_ids=["screen-1"],
    )
    assert saved is not None
    assert not [m for m in store.list_memories() if m["kind"] == "fact"]
    bad = neutral
    bad["outcomes"] = [
        {"content": "Uploaded file successfully", "source_item_ids": ["screen-1"]}
    ]
    with pytest.raises(ValueError):
        store.save_checkpoint(
            conversation,
            through_seq=item["seq"],
            summary=bad,
            expected_memory_revision=0,
            source_item_ids=["screen-1"],
        )


def test_untrusted_fact_cannot_enter_the_restored_checkpoint(store):
    conversation = store.start_conversation()
    item = store.append_item(
        conversation, "page-1", "user", "A page calls me a pilot", kind="screen"
    )
    with pytest.raises(ValueError):
        store.save_checkpoint(
            conversation,
            through_seq=item["seq"],
            summary=summary("The user is a pilot", "page-1"),
            expected_memory_revision=0,
            source_item_ids=["page-1"],
        )
    assert store.latest_checkpoint() is None


@pytest.mark.parametrize(
    "trusted,status,content",
    [
        (False, "completed", "Uploaded the report"),
        (True, "failed", "Uploaded the report"),
        (True, "interrupted", "Uploaded successfully"),
        (True, "failed", "Export failed but upload completed"),
    ],
)
def test_untrusted_or_failed_tool_record_cannot_become_a_success_outcome(
    store, trusted, status, content
):
    conversation = store.start_conversation()
    item = store.append_item(
        conversation,
        "tool-1",
        "tool",
        status,
        kind="tool_result",
        trusted=trusted,
        status=status,
    )
    result = summary()
    result["facts"] = []
    result["outcomes"] = [{"content": content, "source_item_ids": ["tool-1"]}]
    with pytest.raises(ValueError):
        store.save_checkpoint(
            conversation,
            through_seq=item["seq"],
            summary=result,
            expected_memory_revision=0,
            source_item_ids=["tool-1"],
        )


def test_trusted_transcript_fact_and_sdk_function_output_survive_checkpoint(store):
    conversation = store.start_conversation()
    store.append_item(
        conversation,
        "human-1",
        "user",
        "User prefers quiet rooms",
        kind="transcript",
        trusted=True,
    )
    item = store.append_item(
        conversation,
        "tool-1",
        "tool",
        "Run verified",
        kind="function_call_output",
        trusted=True,
        status="verified",
    )
    result = summary()
    result["outcomes"] = [
        {"content": "The run completed", "source_item_ids": ["tool-1"]}
    ]
    store.save_checkpoint(
        conversation,
        through_seq=item["seq"],
        summary=result,
        expected_memory_revision=0,
        source_item_ids=["human-1", "tool-1"],
    )
    assert (
        len([entry for entry in store.list_memories() if entry["kind"] == "fact"]) == 1
    )


def test_direct_automatic_fact_cannot_use_a_webpage_as_human_provenance(store):
    conversation = store.start_conversation()
    store.append_item(
        conversation,
        "screen-1",
        "user",
        "A page says I am a pilot",
        kind="screen",
        trusted=False,
    )
    with pytest.raises(ValueError):
        store.add_fact(
            "The user is a pilot", source="summary", source_item_ids=["screen-1"]
        )
    assert not [entry for entry in store.list_memories() if entry["kind"] == "fact"]


def test_delete_fact_excludes_original_sources_and_rejects_late_summary_after_reopen(
    store, tmp_path
):
    conversation = store.start_conversation("conversation")
    checkpoint(store, conversation)
    fact = next(m for m in store.list_memories() if m["kind"] == "fact")
    revision = store.memory_revision()
    assert store.delete_memory(fact["id"]) is True
    assert store.memory_revision() == revision + 1
    assert store.latest_checkpoint() is None
    assert store.journal(conversation) == []
    assert store.search("quiet") == []
    assert store.excluded(" USER PREFERS QUIET ROOMS ") is True
    store.close()
    reopened = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        with pytest.raises(MemoryConflict):
            checkpoint(reopened, conversation, revision=revision)
        with pytest.raises(ValueError):
            reopened.add_fact(
                "User prefers quiet rooms",
                source="summary",
                source_item_ids=["human-1"],
            )
        assert "quiet rooms" not in reopened.context_snapshot().lower()
    finally:
        reopened.close()


def test_profile_correction_invalidates_summary_without_erasing_explicit_facts(store):
    conversation = store.start_conversation()
    checkpoint(store, conversation)
    store.add_fact("Workspace has two monitors")
    old_revision = store.memory_revision()
    store.save_profile({"name": "Avery", "preferences": ""})
    assert store.latest_checkpoint() is None
    with pytest.raises(MemoryConflict):
        checkpoint(store, conversation, revision=old_revision)
    assert "two monitors" in store.context_snapshot()


def test_clear_conversation_memory_preserves_profile_and_exclusions(store):
    store.save_profile({"name": "Avery"})
    conversation = store.start_conversation()
    checkpoint(store, conversation)
    store.add_fact("Garden has roses")
    revision = store.memory_revision()
    store.clear_conversation_memory()
    assert store.get_profile()["name"] == "Avery"
    assert store.list_memories() == []
    assert store.journal(conversation) == []
    assert store.latest_checkpoint() is None
    assert store.memory_revision() == revision + 1
    assert store.excluded("Garden has roses")


def test_forget_tombstones_matching_legacy_content_even_without_stored_fact(store):
    assert store.forget("Old hobby") is False
    assert store.excluded("The user's old hobby is rowing")
    assert store.memory_revision() == 1
    store.add_fact("New hobby is painting")
    assert store.forget("painting")
    assert store.search("painting") == []


def test_search_finds_archived_detail_missing_from_summary_and_bounds_results(store):
    conversation = store.start_conversation()
    store.append_item(
        conversation, "human-1", "user", "The access shelf is cobalt-blue", trusted=True
    )
    for number in range(8):
        store.add_fact(f"Cobalt sample {number}")
    assert len(store.search("cobalt", limit=3)) == 3
    assert any("access shelf" in text for text in store.search("access shelf"))
    assert len(store.list_memories("cobalt", limit=2)) == 2
    assert len(store.context_snapshot(budget_chars=180)) <= 180
    assert store.search('" OR *') == []


def test_live_status_is_json_and_survives_recovery_as_collecting(store, tmp_path):
    conversation = store.start_conversation("conversation")
    store.set_context_status(
        conversation, {"state": "summarizing", "estimated_tokens": 13000}
    )
    status = store.get_status()
    assert status["status"]["state"] == "summarizing"
    assert status["settings"]["context_budget"] == 16000
    assert status["memory_revision"] == 0
    assert status["checkpoint"] is None
    json.dumps(status)
    store.close()
    reopened = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reopened.get_status()["status"]["state"] == "collecting"
    finally:
        reopened.close()


def test_deleting_unsourced_fact_removes_journal_copy_and_checkpoint(store):
    conversation = store.start_conversation()
    checkpoint(store, conversation)
    explicit = store.add_fact("quiet rooms", source="explicit")
    assert store.delete_memory(explicit["id"])
    assert store.search("quiet") == []
    assert store.journal(conversation) == []
    assert store.latest_checkpoint() is None
    assert "quiet rooms" not in store.context_snapshot().lower()


def test_other_store_sees_current_live_status_without_claiming_recovery(
    store, tmp_path
):
    conversation = store.start_conversation()
    store.set_context_status(
        conversation, {"state": "summarizing", "estimated_tokens": 13000}
    )
    reader = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reader.get_status()["status"]["state"] == "summarizing"
    finally:
        reader.close()


def test_new_conversation_accepts_sources_from_previous_accepted_checkpoint(store):
    previous = store.start_conversation()
    checkpoint(store, previous)
    current = store.start_conversation()
    item = store.append_item(
        current, "human-2", "user", "What about Oslo?", trusted=True
    )
    cumulative = summary()
    cumulative["questions"] = [
        {"content": "What about Oslo?", "source_item_ids": ["human-2"]}
    ]
    saved = store.save_checkpoint(
        current,
        through_seq=item["seq"],
        summary=cumulative,
        expected_memory_revision=store.memory_revision(),
        source_item_ids=["human-1", "human-2"],
    )
    assert saved["summary"]["facts"][0]["content"] == "User prefers quiet rooms"
    assert saved["conversation_id"] == current


def test_checkpoint_transaction_failure_keeps_journal_and_previous_checkpoint(
    store, tmp_path
):
    conversation = store.start_conversation()
    accepted = checkpoint(store, conversation)
    item = store.append_item(
        conversation, "human-2", "user", "I like mountains", trusted=True
    )
    updated = summary()
    updated["facts"].append(
        {"content": "I like mountains", "source_item_ids": ["human-2"]}
    )
    connection = sqlite3.connect(tmp_path / "personal.sqlite3")
    try:
        connection.execute(
            "CREATE TRIGGER fail_fact BEFORE INSERT ON facts BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="synthetic write failure"):
            store.save_checkpoint(
                conversation,
                through_seq=item["seq"],
                summary=updated,
                expected_memory_revision=store.memory_revision(),
                source_item_ids=["human-1", "human-2"],
            )
        assert store.latest_checkpoint(conversation) == accepted
        assert store.journal(conversation)[-1]["text"] == "I like mountains"
        assert not [
            entry
            for entry in store.list_memories()
            if entry["kind"] == "fact" and "mountains" in entry["content"]
        ]
    finally:
        connection.close()


def test_latest_conversation_recovery_uses_usable_journal_not_runtime_status(store):
    earlier = store.start_conversation("earlier")
    store.append_item(earlier, "e1", "user", "An earlier question", trusted=True)
    current = store.start_conversation("current")
    assert store.latest_conversation_id() == earlier
    store.append_item(current, "c1", "user", "A pending question", trusted=True)
    assert store.latest_conversation_id() == current
    store.clear_conversation_memory()
    assert store.latest_conversation_id() is None


def test_correct_fact_atomically_excludes_old_content_and_allows_explicit_revision(
    store,
):
    original = store.add_fact("Workspace uses Python")
    revised = store.correct_fact(
        "Workspace uses Python", "Workspace uses Python 3.14", target="memory"
    )
    assert revised["id"] != original["id"]
    assert store.excluded("Workspace uses Python")
    assert not store.excluded("Workspace uses Python 3.14")
    assert store.list_facts("memory") == [revised]
    assert store.memory_revision() == 2
    assert store.search("3.14") == ["Workspace uses Python 3.14"]


def test_correct_fact_failure_preserves_original_and_revision(store):
    original = store.add_fact("Favorite color is blue")
    store.forget("previously deleted")
    revision = store.memory_revision()
    with pytest.raises(ValueError):
        store.correct_fact(original["content"], "previously deleted")
    assert original in store.list_facts()
    assert store.memory_revision() == revision


def test_recovery_skips_live_owner_before_its_first_status_tick(store, tmp_path):
    conversation = store.start_conversation("live")
    store.append_item(conversation, "live-1", "user", "Still speaking", trusted=True)
    reader = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert reader.latest_conversation_id() == conversation
        assert reader.recoverable_conversation_id() is None
        with pytest.raises(MemoryConflict):
            reader.start_conversation(conversation)
    finally:
        reader.close()


def test_closed_conversation_is_recoverable_until_another_store_claims_it(
    store, tmp_path
):
    conversation = store.start_conversation("recover")
    store.append_item(
        conversation, "recover-1", "user", "Pending question", trusted=True
    )
    store.set_context_status(conversation, {"state": "restored"})
    store.close()
    recovered = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    observer = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        assert recovered.recoverable_conversation_id() == conversation
        assert recovered.start_conversation(conversation) == conversation
        assert observer.recoverable_conversation_id() is None
        assert recovered.journal(conversation)[0]["text"] == "Pending question"
        # An old store closing again cannot release the new store's claim.
        store.close()
        assert observer.recoverable_conversation_id() is None
    finally:
        recovered.close()
        observer.close()


def test_recovery_can_choose_older_closed_journal_when_newest_is_live(tmp_path):
    path = tmp_path / "personal.sqlite3"
    older = PersonalMemoryStore(path)
    newer = PersonalMemoryStore(path)
    observer = PersonalMemoryStore(path)
    try:
        earlier = older.start_conversation("earlier")
        older.append_item(
            earlier, "earlier-1", "user", "An unfinished earlier question", trusted=True
        )
        older.close()
        current = newer.start_conversation("newer")
        newer.append_item(current, "newer-1", "user", "Current question", trusted=True)
        assert observer.latest_conversation_id() == current
        assert observer.recoverable_conversation_id() == earlier
    finally:
        older.close()
        newer.close()
        observer.close()


def test_closed_writer_is_persisted_as_closed_for_other_process_readers(
    store, tmp_path
):
    conversation = store.start_conversation()
    store.append_item(
        conversation, "user-1", "user", "An archived question", trusted=True
    )
    store.close()
    with sqlite3.connect(tmp_path / "personal.sqlite3") as connection:
        status = json.loads(
            connection.execute(
                "SELECT status FROM conversations WHERE id=?", (conversation,)
            ).fetchone()[0]
        )
    assert status["_writer"]["closed"] is True


def test_two_recovery_claims_cannot_own_the_same_conversation(tmp_path):
    path = tmp_path / "personal.sqlite3"
    previous = PersonalMemoryStore(path)
    conversation = previous.start_conversation()
    previous.append_item(
        conversation, "previous-1", "user", "A recoverable question", trusted=True
    )
    previous.close()
    contenders = [PersonalMemoryStore(path), PersonalMemoryStore(path)]
    for contender in contenders:
        assert contender.recoverable_conversation_id() == conversation

    def claim(contender):
        try:
            return contender.start_conversation(conversation)
        except MemoryConflict:
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, contenders))
        assert results.count(conversation) == 1
        assert results.count(None) == 1
    finally:
        for contender in contenders:
            contender.close()


def test_clearing_memory_keeps_live_conversation_owned_for_new_turns(store, tmp_path):
    conversation = store.start_conversation()
    store.append_item(
        conversation, "before-1", "user", "Earlier question", trusted=True
    )
    reader = PersonalMemoryStore(tmp_path / "personal.sqlite3")
    try:
        reader.clear_conversation_memory()
        store.append_item(
            conversation, "after-1", "user", "A new question", trusted=True
        )
        assert reader.latest_conversation_id() == conversation
        assert reader.recoverable_conversation_id() is None
        with pytest.raises(MemoryConflict):
            reader.set_context_status(conversation, {"state": "ready"})
    finally:
        reader.close()
