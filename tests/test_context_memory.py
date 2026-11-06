import asyncio

import pytest

from context_memory import ContextApplyDeferred, ContextMemoryController
from personal_memory import PersonalMemoryStore


def summary(items, previous=None):
    decisions = list((previous or {}).get("decisions", []))
    decisions.append(
        {
            "content": "The project remains in progress.",
            "source_item_ids": [items[0]["item_id"]],
        }
    )
    return {
        "overview": "Project discussion.",
        "facts": [],
        "decisions": decisions,
        "open_threads": [],
        "outcomes": [],
        "questions": [],
    }


class BackgroundSummary:
    def __init__(self, *, gate=None, fail_once=False):
        self.gate = gate
        self.fail_once = fail_once
        self.calls = []
        self.closed = False
        self.max_input_tokens = 24000
        self.max_summary_tokens = 2000

    async def summarize(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate:
            await self.gate.wait()
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("synthetic quota failure")
        return summary(kwargs["items"], kwargs["previous"])

    async def aclose(self):
        self.closed = True


@pytest.fixture
def store(tmp_path):
    instance = PersonalMemoryStore(tmp_path / "memory.db")
    instance.save_settings({"recent_turns": 4})
    yield instance
    instance.close()


def controller_for(store, summarizer=None, *, idle=lambda: True, base_tokens=0):
    applied = []
    invalidated = []

    async def apply_context(accepted, recent, profile):
        # The source checkpoint must already be durable when the provider is reset.
        assert store.latest_checkpoint() is not None or accepted == {
            "overview": "",
            "facts": [],
            "decisions": [],
            "open_threads": [],
            "outcomes": [],
            "questions": [],
        }
        applied.append((accepted, recent, profile))

    controller = ContextMemoryController(
        store,
        summarizer or BackgroundSummary(),
        apply_context=apply_context,
        is_idle=idle,
        invalidate=lambda: invalidated.append(True),
        base_tokens=base_tokens,
        retry_delay_seconds=0,
        flush_timeout_seconds=0.1,
    )
    controller.start("session")
    return controller, applied, invalidated


async def add_turns(controller, count=10, *, size=20, start=0):
    for number in range(start, start + count):
        await controller.record(f"u{number}", "user", "x" * size, trusted=True)


async def settle(controller):
    for _ in range(100):
        await controller.tick()
        if controller.status()["state"] != "summarizing":
            if controller.status()["state"] == "waiting_to_compact":
                await controller.tick()
            return
        await asyncio.sleep(0.001)
    raise AssertionError("background job did not settle")


async def test_threshold_single_flight_and_arrivals_survive_durable_compaction(store):
    gate = asyncio.Event()
    summarizer = BackgroundSummary(gate=gate)
    controller, applied, invalidated = controller_for(store, summarizer)
    await add_turns(controller)
    assert len(store.journal("session")) == 10
    controller.observe_usage(11999)
    await controller.tick()
    assert controller.status()["state"] == "collecting"
    controller.observe_usage(12000)
    await controller.tick()
    await asyncio.sleep(0)
    await controller.tick()
    assert len(summarizer.calls) == 1
    await add_turns(controller, 1, start=10)
    gate.set()
    await settle(controller)
    assert [row["item_id"] for row in applied[0][1]] == ["u6", "u7", "u8", "u9", "u10"]
    assert store.latest_checkpoint()["through_seq"] == 6
    assert len(store.journal("session")) == 11
    assert invalidated == [True]
    assert controller.status()["local_context_restored"] is True
    assert controller.status()["provider_ready"] is False
    assert controller.mark_provider_ready(generation=0) is False
    assert controller.mark_provider_ready(generation=1) is True
    await controller.tick()
    assert len(summarizer.calls) == 1  # stale provider count cannot retrigger.
    await controller.aclose()


async def test_conservative_growth_after_latest_metric_triggers_without_new_metric(
    store,
):
    controller, applied, _ = controller_for(store)
    await add_turns(controller)
    controller.observe_usage(11950)
    await controller.record("growing", "user", "x" * 300, trusted=True)
    await controller.tick()
    await settle(controller)
    assert applied
    await controller.aclose()


async def test_busy_deferral_preserves_committed_checkpoint_until_idle(store):
    idle = False
    controller, applied, _ = controller_for(store, idle=lambda: idle)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert store.latest_checkpoint() is not None
    assert applied == []
    assert controller.status()["state"] == "waiting_to_compact"
    idle = True
    await controller.tick()
    assert applied
    await controller.aclose()


async def test_incomplete_tool_group_blocks_cut_and_apply_then_complete_group_survives(
    store,
):
    controller, applied, _ = controller_for(store)
    await add_turns(controller, 5)
    await controller.record(
        "call",
        "assistant",
        "Build",
        kind="tool_call",
        trusted=True,
        tool_call_id="run1",
        tool_name="build",
        status="running",
    )
    await add_turns(controller, 6, start=5)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert store.latest_checkpoint()["through_seq"] == 5
    assert applied == []
    await controller.record(
        "result",
        "tool",
        "Build failed",
        kind="tool_result",
        trusted=True,
        tool_call_id="run1",
        tool_name="build",
        status="failed",
    )
    await controller.tick()
    assert applied[0][1][0]["item_id"] == "call"
    assert applied[0][1][-1]["item_id"] == "result"
    await controller.aclose()


async def test_tool_groups_crossing_recent_boundary_are_retained_whole(store):
    controller, applied, _ = controller_for(store)
    await add_turns(controller, 5)
    await controller.record(
        "call",
        "assistant",
        "Build",
        kind="tool_call",
        trusted=True,
        tool_call_id="run1",
        status="running",
    )
    await add_turns(controller, 2, start=5)
    await controller.record(
        "result",
        "tool",
        "Done",
        kind="tool_result",
        trusted=True,
        tool_call_id="run1",
        status="completed",
    )
    await add_turns(controller, 3, start=7)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    tail_ids = [row["item_id"] for row in applied[0][1]]
    assert "call" in tail_ids and "result" in tail_ids
    assert store.latest_checkpoint()["through_seq"] == 5
    await controller.aclose()


async def test_failure_retries_from_journal_and_cumulative_checkpoint(store):
    summarizer = BackgroundSummary(fail_once=True)
    controller, applied, _ = controller_for(store, summarizer)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert store.latest_checkpoint() is None
    assert controller.status()["state"] == "error"
    assert len(store.journal("session")) == 10
    await controller.tick(force=True)
    await settle(controller)
    assert applied
    first = store.latest_checkpoint()["summary"]
    await add_turns(controller, 8, start=10)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert summarizer.calls[-1]["previous"] == first
    assert store.latest_checkpoint()["summary"]["decisions"][0] == first["decisions"][0]
    await controller.aclose()


async def test_deletion_during_background_job_cannot_resurrect_checkpoint(store):
    gate = asyncio.Event()
    controller, applied, _ = controller_for(store, BackgroundSummary(gate=gate))
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await asyncio.sleep(0)
    store.clear_conversation_memory()
    gate.set()
    await settle(controller)
    await controller.tick(force=True)
    assert store.latest_checkpoint() is None
    assert len(applied) == 1 and applied[0][0]["overview"] == ""
    assert applied[0][1] == []
    assert store.journal("session") == []
    await controller.aclose()


async def test_disabled_still_journals_but_never_calls_provider(store):
    store.save_settings({"enabled": False})
    summarizer = BackgroundSummary()
    controller, applied, _ = controller_for(store, summarizer)
    await add_turns(controller)
    controller.observe_usage(15000)
    await controller.tick(force=True)
    await controller.flush()
    assert controller.status()["state"] == "disabled"
    assert len(store.journal("session")) == 10
    assert summarizer.calls == [] and applied == []
    await controller.aclose()


async def test_startup_recovers_committed_summary_and_short_final_flush(store):
    controller, _, _ = controller_for(store)
    await add_turns(controller, 2)
    await controller.flush()
    checkpoint = store.latest_checkpoint()
    assert checkpoint["through_seq"] == 2
    await controller.aclose()
    recovered, applied, _ = controller_for(store)
    assert recovered.status()["provider_ready"] is False
    await recovered.tick()
    assert applied[0][0] == checkpoint["summary"]
    await recovered.aclose()


async def test_bounded_shutdown_retains_journal_after_timeout(store):
    gate = asyncio.Event()
    summarizer = BackgroundSummary(gate=gate)
    controller, _, _ = controller_for(store, summarizer)
    await add_turns(controller, 2)
    await asyncio.wait_for(controller.aclose(), timeout=0.5)
    assert store.latest_checkpoint() is None
    assert len(store.journal("session")) == 2
    assert summarizer.closed


async def test_oversized_recent_tail_prepares_additional_prefix_before_apply(store):
    gate = asyncio.Event()
    summarizer = BackgroundSummary(gate=gate)
    controller, applied, _ = controller_for(store, summarizer)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await asyncio.sleep(0)
    await add_turns(controller, 10, size=5000, start=10)
    gate.set()
    await settle(controller)
    # Two recent whole turns fit, but the original 14-turn tail doesn't.
    for _ in range(4):
        await controller.tick()
        await settle(controller)
        if applied:
            break
    assert len(summarizer.calls) >= 2
    assert applied
    assert {row["item_id"] for row in applied[0][1]} >= {"u18", "u19"}
    assert len(store.journal("session")) == 20
    await controller.aclose()


async def test_restart_failure_keeps_checkpoint_recoverable_and_never_ready(store):
    async def unavailable(*args):
        raise RuntimeError("synthetic restart failure")

    controller, _, _ = controller_for(store)
    controller.apply_context = unavailable
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert store.latest_checkpoint() is not None
    assert controller.status()["state"] == "error"
    assert controller.status()["provider_ready"] is False
    await controller.aclose()


async def test_new_explicit_session_restores_prior_memory_and_can_extend_it(store):
    old, _, _ = controller_for(store)
    await add_turns(old, 2)
    await old.flush()
    previous = store.latest_checkpoint()["summary"]
    await old.aclose()
    restored = []

    async def apply_context(accepted, recent, profile):
        restored.append((accepted, recent))

    new = ContextMemoryController(
        store,
        BackgroundSummary(),
        apply_context=apply_context,
        is_idle=lambda: True,
        invalidate=lambda: None,
    )
    new.start("new-explicit-session")
    await new.tick()
    assert restored[0][0] == previous
    for number in range(8):
        await new.record(f"new{number}", "user", "New plan", trusted=True)
    new.observe_usage(13000)
    await new.tick()
    await settle(new)
    assert (
        store.latest_checkpoint()["summary"]["decisions"][0] == previous["decisions"][0]
    )
    await new.aclose()


async def test_disabling_during_summary_prevents_new_checkpoint(store):
    gate = asyncio.Event()
    controller, applied, _ = controller_for(store, BackgroundSummary(gate=gate))
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await asyncio.sleep(0)
    store.save_settings({"enabled": False})
    gate.set()
    await settle(controller)
    await asyncio.sleep(0.01)
    assert store.latest_checkpoint() is None
    assert applied == []
    await controller.aclose()


async def test_deleted_duplicate_event_never_returns_to_local_tail(store):
    controller, applied, _ = controller_for(store)
    await add_turns(controller, 2)
    store.clear_conversation_memory()
    await controller.tick()
    await controller.record("u0", "user", "x" * 20, trusted=True)
    await controller.flush()
    assert store.latest_checkpoint() is None
    assert store.journal("session") == []
    assert len(applied) == 1 and applied[0][0]["overview"] == ""
    assert controller.status()["state"] != "error"
    await controller.aclose()


async def test_provider_event_during_restore_confirms_current_generation_after_callback(
    store,
):
    controller, _, _ = controller_for(store)

    async def apply_context(accepted, recent, profile):
        assert controller.mark_provider_ready(generation=1) is True

    controller.apply_context = apply_context
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.status()["state"] == "ready"
    assert controller.status()["local_context_restored"] is True
    assert controller.status()["provider_ready"] is True
    await controller.aclose()


async def test_arrival_during_apply_is_reapplied_from_journal_at_next_idle(store):
    controller, _, _ = controller_for(store)
    restored = []

    async def apply_context(accepted, recent, profile):
        restored.append([row["item_id"] for row in recent])
        if len(restored) == 1:
            await controller.record("late", "user", "Keep this detail", trusted=True)

    controller.apply_context = apply_context
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    await controller.tick()
    assert restored[-1][-1] == "late"
    assert len(store.journal("session")) == 11
    await controller.aclose()


async def test_no_completed_prefix_reports_budget_error_without_discarding_large_tail(
    store,
):
    controller, applied, _ = controller_for(store)
    await add_turns(controller, 2)
    await controller.flush()
    await add_turns(controller, 2, size=23000, start=2)
    await controller.tick()
    assert applied == []
    assert controller.status()["state"] == "error"
    assert "retained" in controller.status()["last_error"]
    assert len(store.journal("session")) == 4
    await controller.aclose()


async def test_uncorrelated_nonterminal_tool_record_is_never_compacted(store):
    controller, applied, _ = controller_for(store)
    await controller.record(
        "missing-id-call",
        "assistant",
        "Build",
        kind="tool_call",
        trusted=True,
        status="running",
    )
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick(force=True)
    await controller.flush()
    assert store.latest_checkpoint() is None
    assert applied == []
    assert len(store.journal("session")) == 11
    await controller.aclose()


async def test_deletion_during_apply_never_reports_stale_context_as_restored(store):
    controller, _, _ = controller_for(store)

    async def apply_context(accepted, recent, profile):
        store.clear_conversation_memory()

    controller.apply_context = apply_context
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert store.latest_checkpoint() is None
    assert controller.status()["local_context_restored"] is False
    assert controller.status()["provider_ready"] is False
    await controller.aclose()


async def test_provider_readiness_event_does_not_hide_background_or_disabled_state(
    store,
):
    summarizer = BackgroundSummary()
    controller, _, _ = controller_for(store, summarizer)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.mark_provider_ready(generation=1)
    gate = asyncio.Event()
    summarizer.gate = gate
    await add_turns(controller, 8, start=10)
    controller.observe_usage(13000)
    await controller.tick()
    assert controller.mark_provider_ready(generation=1)
    assert controller.status()["state"] == "summarizing"
    store.save_settings({"enabled": False})
    await controller.tick()
    controller.mark_provider_ready(generation=1)
    assert controller.status()["state"] == "disabled"
    gate.set()
    await controller.aclose()


async def test_delete_last_checkpoint_applies_clean_context_at_idle(store):
    controller, applied, invalidated = controller_for(store)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert len(applied) == 1
    store.clear_conversation_memory()
    await controller.tick()
    assert len(applied) == 2
    assert applied[-1][0] == {
        "overview": "",
        "facts": [],
        "decisions": [],
        "open_threads": [],
        "outcomes": [],
        "questions": [],
    }
    assert applied[-1][1] == []
    assert len(invalidated) == 2
    assert controller.status()["local_context_restored"] is True
    assert controller.status()["provider_ready"] is False
    await controller.aclose()


async def test_provider_reconnecting_revokes_previous_generation_and_keeps_local_restore(
    store,
):
    controller, _, _ = controller_for(store)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.mark_provider_ready(generation=1)
    assert controller.provider_reconnecting() == 2
    assert controller.status()["local_context_restored"] is True
    assert controller.status()["provider_ready"] is False
    assert controller.status()["state"] == "restored"
    assert controller.mark_provider_ready(generation=1) is False
    assert controller.mark_provider_ready(generation=2) is True
    await controller.aclose()


async def test_child_replacement_during_controller_compaction_keeps_started_generation(
    store,
):
    controller, _, _ = controller_for(store)

    async def apply_context(accepted, recent, profile):
        assert controller.status()["state"] == "compacting"
        assert controller.provider_reconnecting() == 1
        assert controller.status()["state"] == "compacting"
        assert controller.mark_provider_ready(generation=1)

    controller.apply_context = apply_context
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.status()["state"] == "ready"
    assert controller.status()["provider_generation"] == 1
    await controller.aclose()


async def test_provider_reconnect_preserves_inflight_summary_and_disabled_work_states(
    store,
):
    summarizer = BackgroundSummary()
    controller, _, _ = controller_for(store, summarizer)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.mark_provider_ready(generation=1)
    gate = asyncio.Event()
    summarizer.gate = gate
    await add_turns(controller, 8, start=10)
    controller.observe_usage(13000)
    await controller.tick()
    assert controller.provider_reconnecting() == 2
    assert controller.status()["state"] == "summarizing"
    assert controller.status()["local_context_restored"] is True
    assert controller.mark_provider_ready(generation=1) is False
    store.save_settings({"enabled": False})
    await controller.tick()
    assert controller.provider_reconnecting() == 3
    assert controller.status()["state"] == "disabled"
    gate.set()
    await controller.aclose()


async def test_public_local_restore_marker_does_not_claim_provider_readiness(store):
    controller, _, _ = controller_for(store)
    assert controller.mark_context_restored(generation=99) is False
    assert controller.status()["local_context_restored"] is False
    generation = controller.provider_reconnecting()
    assert controller.mark_context_restored(generation=generation) is True
    assert controller.status()["state"] == "restored"
    assert controller.status()["provider_ready"] is False
    assert controller.mark_provider_ready(generation=generation) is True
    await controller.aclose()


async def test_fatal_provider_failure_invalidates_readiness_and_keeps_durable_checkpoint(
    store,
):
    controller, _, _ = controller_for(store)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    checkpoint = store.latest_checkpoint()
    assert controller.mark_provider_ready(generation=1)
    controller.provider_failed("Bearer sensitive-token: user-provided details")
    assert controller.status()["state"] == "error"
    assert controller.status()["provider_ready"] is False
    assert controller.status()["local_context_restored"] is False
    assert (
        controller.status()["last_error"] == "Voice connection failed; journal retained"
    )
    assert store.latest_checkpoint() == checkpoint
    assert len(store.journal("session")) == 10
    await controller.aclose()


async def test_resumed_human_input_defers_without_failure_then_retries_at_idle(store):
    idle = True
    controller, _, _ = controller_for(store, idle=lambda: idle)
    calls = []

    async def apply_context(accepted, recent, profile):
        nonlocal idle
        calls.append(accepted)
        if len(calls) == 1:
            idle = False
            raise ContextApplyDeferred("Human input resumed during context preparation")

    controller.apply_context = apply_context
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    checkpoint = store.latest_checkpoint()
    assert controller.status()["state"] == "waiting_to_compact"
    assert controller.status()["last_error"] is None
    assert controller.status()["provider_ready"] is False
    assert controller.status()["local_context_restored"] is False
    await controller.record(
        "resumed", "user", "Keep working on the same plan", trusted=True
    )
    await controller.tick()
    assert len(calls) == 1
    idle = True
    await controller.tick()
    assert len(calls) == 2 and calls[1] == checkpoint["summary"]
    assert controller.status()["state"] == "restored"
    assert controller.status()["last_error"] is None
    await controller.aclose()


async def test_journal_failure_recovers_only_its_storage_error(store):
    controller, _, _ = controller_for(store)
    controller.journal_failed()
    assert controller.status()["state"] == "error"
    assert (
        controller.status()["last_error"]
        == "Conversation storage unavailable; waiting to save captured turns"
    )
    controller.journal_recovered()
    assert controller.status()["state"] == "collecting"
    assert controller.status()["last_error"] is None
    controller.provider_failed()
    controller.journal_failed()
    controller.journal_recovered()
    assert controller.status()["state"] == "error"
    assert (
        controller.status()["last_error"] == "Voice connection failed; journal retained"
    )
    controller.journal_failed()
    controller.provider_failed("Voice restart failed; journal retained")
    controller.journal_recovered()
    assert controller.status()["last_error"] == "Voice restart failed; journal retained"
    await controller.aclose()


async def test_journal_recovery_retains_checkpoint_and_requires_fresh_readiness(store):
    controller, _, _ = controller_for(store)
    await add_turns(controller)
    controller.observe_usage(13000)
    await controller.tick()
    await settle(controller)
    assert controller.mark_provider_ready(generation=1)
    checkpoint = store.latest_checkpoint()
    controller.journal_failed()
    assert controller.mark_provider_ready(generation=1) is False
    assert controller.status()["provider_ready"] is False
    controller.journal_recovered()
    assert controller.status()["state"] == "restored"
    assert controller.status()["local_context_restored"] is True
    assert store.latest_checkpoint() == checkpoint
    assert controller.mark_provider_ready(generation=1) is True
    await controller.aclose()
