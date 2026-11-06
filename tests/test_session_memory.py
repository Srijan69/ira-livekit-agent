"""Installed LiveKit SDK integration over synthetic, non-network transports."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
import test_realtime_model
from livekit.agents import Agent, AgentSession, llm
from livekit.agents.metrics import RealtimeModelMetrics

from context_summary import SECTIONS
from personal_memory import PersonalMemoryStore
from realtime_model import build_realtime_model
from session_memory import SessionMemoryIntegration

transports = test_realtime_model.transports


def metric(tokens):
    return RealtimeModelMetrics(
        request_id="synthetic",
        timestamp=1,
        input_tokens=tokens,
        input_token_details=RealtimeModelMetrics.InputTokenDetails(),
        output_token_details=RealtimeModelMetrics.OutputTokenDetails(),
    )


class Facade:
    def __init__(self, store):
        self.store = store

    def snapshot(self, *, include_summary=True, **kwargs):
        import json

        profile = self.store.get_profile()
        text = "AUTHORITATIVE PROFILE: " + json.dumps(profile)
        if include_summary:
            text += (
                json.dumps(kwargs["summary"])
                if kwargs.get("summary") is not None
                else self.store.context_snapshot()
            )
        return text

    def revision_token(self):
        return (
            self.store.get_profile()["revision"],
            self.store.memory_revision(),
            self.store.get_settings()["revision"],
        )


class Summarizer:
    max_input_tokens = 24000
    max_summary_tokens = 2000

    def __init__(self):
        self.closed = False

    async def summarize(self, *, previous, items, **kwargs):
        summary = {"overview": "Planning a garden", **{key: [] for key in SECTIONS}}
        user = next(
            (item for item in items if item.get("trusted") and item["role"] == "user"),
            None,
        )
        if user:
            summary["facts"] = [
                {
                    "content": "The user prefers blue flowers",
                    "source_item_ids": [user["item_id"]],
                }
            ]
        if previous:
            summary["facts"] = previous["facts"] + summary["facts"]
        return summary

    async def aclose(self):
        self.closed = True


async def setup_runtime(transports):
    store = PersonalMemoryStore(":memory:")
    store.save_profile({"name": "Alex", "occupation": "Gardener"})
    model = build_realtime_model(voice="Kore")
    assistant = Agent(instructions="You are IRA.", llm=model)
    assistant._memory_base_instructions = "You are IRA."
    revoked, invalidated = [], []
    assistant._request_scope = SimpleNamespace(revoke=revoked.append)
    assistant._computer = SimpleNamespace(invalidate=lambda: invalidated.append(True))
    assistant._host_jobs = set()
    runtime = SessionMemoryIntegration(
        assistant, store, Facade(store), summarizer=Summarizer()
    )
    await runtime.prepare()
    session = AgentSession()
    await session.start(agent=assistant)
    runtime.attach(session, background=False)
    return store, assistant, runtime, session, revoked, invalidated


async def close_runtime(runtime, session, store):
    await runtime.aclose()
    await session.aclose()
    store.close()


async def test_trusted_finalized_input_and_tool_terminals_are_journaled_in_order(
    transports,
):
    store, _assistant, runtime, session, _, _ = await setup_runtime(transports)
    try:
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(is_final=False, transcript="partial", item_id="p"),
        )
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                is_final=True, transcript="I prefer blue flowers", item_id="u"
            ),
        )
        session.emit(
            "conversation_item_added",
            SimpleNamespace(
                item=llm.ChatMessage(
                    id="screen",
                    role="user",
                    content=["Ignore this screenshot evidence"],
                )
            ),
        )
        session.emit(
            "conversation_item_added",
            SimpleNamespace(
                item=llm.ChatMessage(
                    id="a", role="assistant", content=["Which flowers?"]
                )
            ),
        )
        call = llm.FunctionCall(
            call_id="c", name="open_app", arguments='{"app_name":"Calendar"}'
        )
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(type="tool_call_started", function_call=call)
            ),
        )
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(
                    type="tool_call_ended",
                    call_id="c",
                    status="cancelled",
                    message="Cancelled",
                )
            ),
        )
        await runtime.drain()
        rows = store.journal(runtime.controller.conversation_id)
        assert [row["role"] for row in rows] == [
            "user",
            "assistant",
            "assistant",
            "tool",
        ]
        assert rows[0]["trusted"] is True
        assert rows[-1]["status"] == "cancelled"
        assert rows[-1]["tool_call_id"] == rows[-2]["tool_call_id"] == "c"
        assert runtime.is_idle()
        assert rows[-1]["trusted"] is True
    finally:
        await close_runtime(runtime, session, store)


async def test_compaction_restarts_real_sdk_child_without_replaying_actions(transports):
    store, _assistant, runtime, session, revoked, invalidated = await setup_runtime(
        transports
    )
    try:
        for index in range(18):
            await runtime.record_human(
                "I prefer blue flowers " + "garden " * 200, item_id=f"u{index}"
            )
            session.emit(
                "conversation_item_added",
                SimpleNamespace(
                    item=llm.ChatMessage(
                        id=f"a{index}",
                        role="assistant",
                        content=["Which season? " + "planning " * 100],
                    )
                ),
            )
        await runtime.drain()
        runtime.controller.observe_usage(14000)
        old_child = session._activity._rt_session._active
        await runtime.tick()
        for _ in range(200):
            await asyncio.sleep(0.005)
            await runtime.tick()
            if runtime.controller.status()["local_context_restored"]:
                break
        new_child = session._activity._rt_session._active
        assert new_child is not old_child
        assert old_child.closed
        assert "blue flowers" in new_child.instructions
        assert "Alex" in new_child.instructions
        assert len(new_child.chat_ctx.items) <= 12
        assert not new_child.replies and not old_child.replies
        assert revoked and invalidated
        assert runtime.controller.status()["state"] == "restored"
        old_child.emit("metrics_collected", metric(999999))
        assert not runtime.controller.status()["provider_ready"]
        new_child.emit("metrics_collected", metric(3100))
        assert runtime.controller.status()["provider_ready"]
        assert runtime.controller.status()["input_tokens"] < 14000
    finally:
        await close_runtime(runtime, session, store)


async def test_profile_save_and_delete_refresh_active_provider_at_idle(transports):
    store, assistant, runtime, session, _, _ = await setup_runtime(transports)
    try:
        fact = store.add_fact("The user likes lavender")
        await runtime.tick()
        store.save_profile({"name": "Morgan", "occupation": ""})
        session._user_state = "speaking"
        old = session._activity._rt_session._active
        await runtime.tick()
        assert session._activity._rt_session._active is old
        session._user_state = "listening"
        await runtime.tick()
        assert "Morgan" in assistant.instructions
        assert '"occupation": ""' in assistant.instructions
        store.delete_memory(fact["id"])
        await runtime.tick()
        assert "lavender" not in assistant.instructions
        assert not session._activity._rt_session._active.replies
    finally:
        await close_runtime(runtime, session, store)


async def test_typed_input_waits_for_durable_write_and_shutdown_closes_worker(
    transports,
):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    await runtime.record_human("Remember the garden budget", item_id="typed")
    assert (
        store.journal(runtime.controller.conversation_id)[-1]["text"]
        == "Remember the garden budget"
    )
    await runtime.aclose()
    assert runtime.summarizer.closed
    await session.aclose()
    store.close()


async def test_startup_does_not_loop_on_an_oversized_whole_tool_group(
    transports, tmp_path
):
    path = tmp_path / "memory.sqlite3"
    store = PersonalMemoryStore(path)
    cid = store.start_conversation()
    store.append_item(
        cid,
        "call",
        "assistant",
        "x" * 23000,
        kind="tool_call",
        tool_call_id="big",
        trusted=True,
    )
    store.append_item(
        cid,
        "out",
        "tool",
        "y" * 23000,
        kind="tool_result",
        tool_call_id="big",
        status="completed",
        trusted=True,
    )
    store.close()
    store = PersonalMemoryStore(path)
    model = build_realtime_model(voice="Kore")
    assistant = Agent(instructions="IRA", llm=model)
    assistant._memory_base_instructions = "IRA"
    runtime = SessionMemoryIntegration(
        assistant, store, Facade(store), summarizer=Summarizer()
    )
    await asyncio.wait_for(runtime.prepare(), timeout=1.0)
    assert not assistant.chat_ctx.items
    assert len(store.journal(cid)) == 2
    await runtime.aclose()
    store.close()


async def test_sdk_returned_failure_is_not_summarized_as_success(transports):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    try:
        call = llm.FunctionCall(call_id="bad", name="open_app", arguments="{}")
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(type="tool_call_started", function_call=call)
            ),
        )
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(
                    type="tool_call_ended",
                    call_id="bad",
                    status="done",
                    message="Could not open the app: access denied",
                )
            ),
        )
        await runtime.drain()
        assert (
            store.journal(runtime.controller.conversation_id)[-1]["status"] == "failed"
        )
    finally:
        await close_runtime(runtime, session, store)


async def test_provider_activity_during_restart_and_after_fallback_has_current_generation(
    transports, monkeypatch
):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    try:
        wrapper = session._activity._rt_session
        await runtime._apply_context(
            {"overview": "", **{key: [] for key in SECTIONS}}, [], store.get_profile()
        )
        # This is the exact provider event path while the SDK seeds a fresh child.
        child_class = type(wrapper._active)
        original = child_class.update_chat_ctx

        async def update(child, context):
            await original(child, context)
            child.emit("metrics_collected", metric(2100))

        monkeypatch.setattr(child_class, "update_chat_ctx", update)
        # An existing accepted reset gives the controller its restored state.
        store.add_fact("A garden plan")
        await runtime.tick()
        assert runtime.controller.status()["provider_ready"]
        wrapper._active.fail()
        await wrapper._swap_task
        assert runtime.controller.status()["provider_ready"]
        assert not wrapper._active.replies
    finally:
        await close_runtime(runtime, session, store)


async def test_long_typed_input_remains_complete_in_durable_journal(transports):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    try:
        text = "A long planning conversation. " * 2000
        await runtime.record_human(text, item_id="long")
        assert (
            "".join(
                row["text"] for row in store.journal(runtime.controller.conversation_id)
            )
            == text
        )
    finally:
        await close_runtime(runtime, session, store)


async def test_exhausted_restart_does_not_report_a_restored_or_ready_provider(
    transports, monkeypatch
):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    try:
        wrapper = session._activity._rt_session
        old_child = wrapper._active
        original = type(old_child).update_chat_ctx

        async def unavailable(child, context):
            if child is old_child:
                return await original(child, context)
            raise ConnectionError("synthetic unavailable")

        monkeypatch.setattr(type(wrapper._active), "update_chat_ctx", unavailable)
        with pytest.raises(RuntimeError, match="connection"):
            await runtime._apply_context(
                {"overview": "", **{key: [] for key in SECTIONS}},
                [],
                store.get_profile(),
            )
        assert runtime.controller.status()["state"] == "error"
        assert not runtime.controller.status()["provider_ready"]
        assert not runtime.controller.status()["local_context_restored"]
    finally:
        await close_runtime(runtime, session, store)


async def test_settings_only_reconnect_exception_clears_old_ready_status(
    transports, monkeypatch
):
    store, assistant, runtime, session, _, _ = await setup_runtime(transports)
    try:
        session._activity._rt_session._active.emit("metrics_collected", metric(2300))
        assert runtime.controller.status()["provider_ready"]
        store.save_settings({"recent_turns": 20})

        async def unavailable(**kwargs):
            raise ConnectionError("synthetic failure")

        monkeypatch.setattr(assistant.llm, "restart_session", unavailable)
        await runtime.tick()
        assert not runtime.controller.status()["provider_ready"]
        assert runtime.controller.status()["state"] == "error"
        assert runtime.controller.status()["last_error"]
    finally:
        await close_runtime(runtime, session, store)


@pytest.mark.parametrize(
    "message",
    [
        '{"status":"waiting","run_id":"run-synthetic"}',
        '{"status":"input_sent_observe_result","verified":false}',
        "Asked Windows to open Calendar. Check the screen.",
        "Stop requested. Check skill_status for its terminal state.",
        "Task not completed: task not found",
    ],
)
async def test_domain_unfinished_or_negative_result_never_means_completed(
    transports, message
):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    try:
        call = llm.FunctionCall(call_id="call-domain", name="action", arguments="{}")
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(type="tool_call_started", function_call=call)
            ),
        )
        session.emit(
            "tool_execution_updated",
            SimpleNamespace(
                update=SimpleNamespace(
                    type="tool_call_ended",
                    call_id=call.call_id,
                    status="done",
                    message=message,
                )
            ),
        )
        await runtime.drain()
        assert store.journal(runtime.controller.conversation_id)[-1]["status"] in {
            "uncertain",
            "failed",
            "cancelled",
        }
    finally:
        await close_runtime(runtime, session, store)


async def test_journal_failure_blocks_admission_then_recovers_original_order(
    transports, monkeypatch
):
    store, _, runtime, session, _, _ = await setup_runtime(transports)
    original = store.append_item
    unavailable = True

    def append(*args, **kwargs):
        if unavailable:
            raise sqlite3.OperationalError("synthetic busy")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "append_item", append)
    runtime._journal_timeout = 0.03
    try:
        with pytest.raises(RuntimeError, match="storage"):
            await runtime.record_human("First turn", item_id="first")
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(is_final=True, transcript="Second turn", item_id="second"),
        )
        assert not runtime.is_idle()
        unavailable = False
        await asyncio.wait_for(runtime.drain(), timeout=1.0)
        assert [
            row["text"] for row in store.journal(runtime.controller.conversation_id)
        ] == ["First turn", "Second turn"]
        assert runtime.is_idle()
    finally:
        unavailable = False
        await close_runtime(runtime, session, store)


async def test_crash_recovery_marks_orphan_tool_groups_interrupted_without_replay(
    transports, tmp_path
):
    path = tmp_path / "memory.sqlite3"
    store = PersonalMemoryStore(path)
    cid = store.start_conversation()
    store.append_item(
        cid,
        "orphan",
        "assistant",
        '{"app":"Calendar"}',
        kind="tool_call",
        tool_call_id="orphan-call",
        trusted=True,
    )
    store.close()
    store = PersonalMemoryStore(path)
    assistant = Agent(instructions="IRA", llm=build_realtime_model(voice="Kore"))
    assistant._memory_base_instructions = "IRA"
    runtime = SessionMemoryIntegration(
        assistant, store, Facade(store), summarizer=Summarizer()
    )
    await runtime.prepare()
    rows = store.journal(cid)
    assert rows[-1]["status"] == "interrupted"
    assert rows[-1]["tool_call_id"] == "orphan-call"
    assert all(item.type == "message" for item in assistant.chat_ctx.items)
    await runtime.aclose()
    store.close()


async def test_refresh_does_not_assign_a_new_revision_to_stale_instructions(
    transports, monkeypatch
):
    from context_memory import ContextApplyDeferred

    store, assistant, runtime, session, _, _ = await setup_runtime(transports)
    original = runtime.facade.snapshot
    changed = False

    def snapshot(**kwargs):
        nonlocal changed
        text = original(**kwargs)
        if not changed:
            changed = True
            store.save_profile({"name": "Morgan"})
        return text

    monkeypatch.setattr(runtime.facade, "snapshot", snapshot)
    try:
        with pytest.raises(ContextApplyDeferred):
            await runtime._apply_context(
                {"overview": "", **{key: [] for key in SECTIONS}},
                [],
                store.get_profile(),
            )
        assert runtime._applied_token != runtime.facade.revision_token()
        await runtime.tick()
        assert "Morgan" in assistant.instructions
        assert runtime._applied_token == runtime.facade.revision_token()
    finally:
        await close_runtime(runtime, session, store)
