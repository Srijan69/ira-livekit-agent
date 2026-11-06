"""Argument repair exercised through LiveKit's real preparation/execution boundary."""

import asyncio
import json
from enum import Enum
from typing import Annotated, Literal

import pytest
from livekit.agents import Agent, AgentSession, RunContext, ToolError, function_tool
from livekit.agents.llm import FunctionCall, ToolContext, ToolFlag
from livekit.agents.llm.utils import prepare_function_arguments
from livekit.agents.voice.speech_handle import SpeechHandle
from livekit.agents.voice.tool_executor import _ToolExecutor
from pydantic import Field

import tool_dispatch


def repaired(tool):
    return tool_dispatch.repair_function_tool(tool)


def context(tool, arguments="{}", *, call_id="boundary", session=None):
    return RunContext(
        session=session or AgentSession(),
        speech_handle=SpeechHandle.create(),
        function_call=FunctionCall(
            name=tool.info.name, call_id=call_id, arguments=arguments
        ),
    )


async def invoke(tool, arguments, ctx=None):
    args, kwargs = prepare_function_arguments(
        fnc=tool, json_arguments=arguments, call_ctx=ctx or context(tool)
    )
    return await tool(*args, **kwargs)


class HarmlessAgent(Agent):
    def __init__(self, effects):
        self.effects = effects
        super().__init__(instructions="Offline argument boundary")

    @function_tool(flags=ToolFlag.CANCELLABLE, on_duplicate="reject")
    async def open_app(self, context: RunContext, app_name: str, count: int = 1) -> str:
        """Record an inert launch."""
        self.effects.append((app_name, count, context))
        return "recorded"


@pytest.mark.parametrize("arguments", ['{"name":"Notepad"}', '{"app":"Notepad"}'])
async def test_alias_is_repaired_before_canonical_required_field_validation(arguments):
    effects = []
    original = HarmlessAgent(effects).tools[0]
    tool = repaired(original)
    ctx = context(tool)
    assert await invoke(tool, arguments, ctx) == "recorded"
    assert effects == [("Notepad", 1, ctx)]


async def test_canonical_defaults_types_context_and_direct_python_calls():
    effects = []
    original = HarmlessAgent(effects).tools[0]
    tool = repaired(original)
    ctx = context(tool)
    await invoke(tool, '{"app_name":"Notepad","count":"2"}', ctx)
    await tool(ctx, "Calculator", 3)
    await tool(context=ctx, app_name="Paint")
    assert effects == [("Notepad", 2, ctx), ("Calculator", 3, ctx), ("Paint", 1, ctx)]


@pytest.mark.parametrize(
    "arguments",
    [
        "{}",
        '{"name":12}',
        '{"name":"Notepad","count":"several"}',
        '{"name":"Notepad","unknown":"ignored?"}',
        '{"name":"Paint","app_name":"Notepad"}',
        '{"app_name":"Notepad","name":"Paint"}',
        '{"name":"Notepad","app":"Paint"}',
        '["Notepad"]',
        "unrecoverable",
    ],
)
async def test_invalid_provider_arguments_never_reach_the_operation(arguments):
    effects = []
    tool = repaired(HarmlessAgent(effects).tools[0])
    with pytest.raises(ToolError):
        await invoke(tool, arguments)
    assert effects == []


async def test_identical_alias_and_canonical_values_are_unambiguous():
    effects = []
    tool = repaired(HarmlessAgent(effects).tools[0])
    await invoke(tool, '{"name":"Notepad","app_name":"Notepad"}')
    assert [(name, count) for name, count, _ in effects] == [("Notepad", 1)]


@pytest.mark.parametrize("live_schema", [False, True])
def test_provider_schema_flags_and_policy_remain_canonical(live_schema):
    original = HarmlessAgent([]).tools[0]
    tool = repaired(original)
    assert ToolContext([tool]).parse_function_tools(
        "google", use_parameters_json_schema=not live_schema
    ) == ToolContext([original]).parse_function_tools(
        "google", use_parameters_json_schema=not live_schema
    )
    assert tool.info.flags == original.info.flags
    assert tool.info.on_duplicate == original.info.on_duplicate
    assert tool.info.duplicate_scope == original.info.duplicate_scope


async def test_enum_constraints_and_inputs_json_canonical_type_are_validated():
    effects = []

    @function_tool
    async def run_skill(
        name: str,
        inputs_json: dict[str, int],
        action: Literal["read", "write"] = "read",
        count: Annotated[int, Field(ge=1)] = 1,
    ):
        effects.append((name, inputs_json, action, count))

    tool = repaired(run_skill)
    await invoke(tool, '{"skill":"inspect","inputs":{"x":2}}')
    assert effects == [("inspect", {"x": 2}, "read", 1)]
    for arguments in [
        '{"skill":"inspect","inputs":{"x":2},"action":"delete"}',
        '{"skill":"inspect","inputs":{"x":2},"count":0}',
    ]:
        with pytest.raises(ToolError):
            await invoke(tool, arguments)
    assert len(effects) == 1


async def test_string_inputs_json_accepts_existing_dictionary_alias_repair():
    effects = []

    @function_tool
    async def run_skill(name: str, inputs_json: str = "{}"):
        effects.append((name, json.loads(inputs_json)))

    tool = repaired(run_skill)
    await invoke(tool, '{"skill":"inspect","inputs":{"x":2}}')
    assert effects == [("inspect", {"x": 2})]


def test_direct_argument_repair_rejects_conflicting_aliases_in_both_orders():
    for arguments in [
        {"app_name": "Notepad", "name": "Paint"},
        {"name": "Paint", "app_name": "Notepad"},
    ]:
        with pytest.raises(ValueError, match="Conflicting"):
            tool_dispatch.repair_tool_arguments(arguments, expected_params={"app_name"})


async def test_existing_raw_tools_and_typed_tools_without_aliases_remain_usable():
    seen = []

    @function_tool(
        raw_schema={
            "name": "custom",
            "description": "User supplied raw payload",
            "parameters": {
                "type": "object",
                "properties": {"custom": {"type": "integer"}},
            },
        }
    )
    async def raw(raw_arguments: dict, context: RunContext):
        seen.append((raw_arguments, context))

    @function_tool
    async def typed(custom: int):
        seen.append(custom)

    raw_tool, typed_tool = repaired(raw), repaired(typed)
    assert raw_tool is raw
    assert typed_tool is typed
    ctx = context(raw_tool)
    await invoke(raw_tool, '{"custom":4}', ctx)
    await invoke(typed_tool, '{"custom":5}')
    assert seen == [({"custom": 4}, ctx), 5]


async def test_repair_of_scope_facade_is_idempotent_and_keeps_owner_marker():
    effects = []
    original = HarmlessAgent(effects).tools[0]
    owner = object()
    original._ira_scope_facade = owner
    tool = repaired(original)
    assert repaired(tool) is tool
    assert tool._ira_scope_facade is owner


@pytest.mark.parametrize("scope", ["name", "name_and_args"])
async def test_real_executor_rejects_duplicate_with_alias_default_equivalence(scope):
    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()

    @function_tool(
        on_duplicate="reject", duplicate_scope=scope, flags=ToolFlag.CANCELLABLE
    )
    async def open_app(app_name: str, count: int = 1):
        effects.append((app_name, count))
        entered.set()
        await release.wait()
        return "done"

    tool = repaired(open_app)
    executor = _ToolExecutor()
    session = AgentSession()
    first_args = {"app_name": "Notepad"}
    first_ctx = context(tool, json.dumps(first_args), call_id="first", session=session)
    first = asyncio.create_task(
        executor.execute(tool=tool, run_ctx=first_ctx, raw_arguments=first_args)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second_args = {"name": "Notepad", "count": "1"}
        second_ctx = context(
            tool, json.dumps(second_args), call_id="second", session=session
        )
        result = await asyncio.wait_for(
            executor.execute(tool=tool, run_ctx=second_ctx, raw_arguments=second_args),
            1,
        )
        assert "already running" in result
        assert effects == [("Notepad", 1)]
        release.set()
        assert await first == "done"
    finally:
        release.set()
        await executor.aclose()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("cancellable", [False, True])
async def test_real_executor_replacement_honors_cancellation_policy(cancellable):
    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = []

    @function_tool(
        on_duplicate="replace",
        duplicate_scope="name_and_args",
        flags=ToolFlag.CANCELLABLE if cancellable else ToolFlag.NONE,
    )
    async def open_app(context: RunContext, app_name: str, count: int = 1):
        effects.append((context.function_call.call_id, app_name, count))
        entered.set()
        try:
            if context.function_call.call_id == "first":
                await release.wait()
        except asyncio.CancelledError:
            cancelled.append(context.function_call.call_id)
            raise
        return "done"

    tool = repaired(open_app)
    executor = _ToolExecutor()
    session = AgentSession()
    first_args = {"app_name": "Notepad"}
    first_ctx = context(tool, json.dumps(first_args), call_id="first", session=session)
    first = asyncio.create_task(
        executor.execute(tool=tool, run_ctx=first_ctx, raw_arguments=first_args)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second_args = {"name": "Notepad", "count": "1"}
        second_ctx = context(
            tool, json.dumps(second_args), call_id="second", session=session
        )
        second = executor.execute(
            tool=tool, run_ctx=second_ctx, raw_arguments=second_args
        )
        if cancellable:
            assert await asyncio.wait_for(second, 1) == "done"
            assert cancelled == ["first"]
            assert effects == [("first", "Notepad", 1), ("second", "Notepad", 1)]
        else:
            with pytest.raises(ToolError, match="not cancellable"):
                await asyncio.wait_for(second, 1)
            assert cancelled == []
            assert effects == [("first", "Notepad", 1)]
        release.set()
        await first
    finally:
        release.set()
        await executor.aclose()
        await asyncio.gather(first, return_exceptions=True)


async def test_real_executor_confirmation_and_distinct_arguments_preserve_concurrency():
    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()

    @function_tool(on_duplicate="confirm", duplicate_scope="name_and_args")
    async def open_app(context: RunContext, app_name: str, count: int = 1):
        effects.append((context.function_call.call_id, app_name, count))
        if context.function_call.call_id == "first":
            entered.set()
            await release.wait()
        return "done"

    tool = repaired(open_app)
    executor = _ToolExecutor()
    session = AgentSession()
    first_args = {"app_name": "Notepad"}
    first_ctx = context(tool, json.dumps(first_args), call_id="first", session=session)
    first = asyncio.create_task(
        executor.execute(tool=tool, run_ctx=first_ctx, raw_arguments=first_args)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for call_id, arguments in [
            ("denied", {"name": "Notepad", "count": "1"}),
            (
                "confirmed",
                {"name": "Notepad", "count": "1", "lk_agents_confirm_duplicate": True},
            ),
            ("distinct", {"name": "Paint"}),
        ]:
            ctx = context(tool, json.dumps(arguments), call_id=call_id, session=session)
            result = await asyncio.wait_for(
                executor.execute(tool=tool, run_ctx=ctx, raw_arguments=arguments), 1
            )
            assert (
                ("already running" in result)
                if call_id == "denied"
                else result == "done"
            )
        assert effects == [
            ("first", "Notepad", 1),
            ("confirmed", "Notepad", 1),
            ("distinct", "Paint", 1),
        ]
        release.set()
        await first
    finally:
        release.set()
        await executor.aclose()
        await asyncio.gather(first, return_exceptions=True)


async def test_real_executor_cancellation_emits_terminal_and_clears_duplicate_guard():
    effects = []
    events = []
    entered = asyncio.Event()
    release = asyncio.Event()

    @function_tool(
        on_duplicate="reject",
        duplicate_scope="name_and_args",
        flags=ToolFlag.CANCELLABLE,
    )
    async def open_app(context: RunContext, app_name: str, count: int = 1):
        effects.append(context.function_call.call_id)
        if context.function_call.call_id == "first":
            entered.set()
            await release.wait()
        return "done"

    tool = repaired(open_app)
    executor = _ToolExecutor()
    session = AgentSession()
    session.on("tool_execution_updated", events.append)
    first_ctx = context(tool, call_id="first", session=session)
    first = asyncio.create_task(
        executor.execute(
            tool=tool, run_ctx=first_ctx, raw_arguments={"app_name": "Notepad"}
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert await executor.cancel("first") is True
        await first
        next_ctx = context(tool, call_id="next", session=session)
        assert (
            await executor.execute(
                tool=tool,
                run_ctx=next_ctx,
                raw_arguments={"name": "Notepad", "count": "1"},
            )
            == "done"
        )
        # Executor terminal callbacks run on the following event-loop iteration.
        await asyncio.sleep(0)
        assert effects == ["first", "next"]
        ended = [event.update for event in events if hasattr(event.update, "status")]
        assert [(event.call_id, event.status) for event in ended] == [
            ("first", "cancelled"),
            ("next", "done"),
        ]
    finally:
        release.set()
        await executor.aclose()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("first_name_key", ["app_name", "name"])
async def test_real_executor_enum_dictionary_keys_preserve_calls_and_duplicates(
    first_name_key,
):
    class InputKey(Enum):
        COUNT = "count"

    effects = []
    entered = asyncio.Event()
    release = asyncio.Event()

    @function_tool(on_duplicate="reject", duplicate_scope="name_and_args")
    async def open_app(
        context: RunContext, app_name: str, inputs_json: dict[InputKey, int]
    ):
        count = inputs_json[InputKey.COUNT]
        effects.append((context.function_call.call_id, app_name, count))
        if context.function_call.call_id == "first":
            entered.set()
            await release.wait()
        return f"{app_name}:{count}"

    tool = repaired(open_app)
    executor = _ToolExecutor()
    session = AgentSession()
    baseline_args = {"app_name": "Notepad", "inputs_json": {"count": 2}}
    baseline_ctx = context(
        open_app, json.dumps(baseline_args), call_id="baseline", session=session
    )
    assert (
        await executor.execute(
            tool=open_app, run_ctx=baseline_ctx, raw_arguments=baseline_args
        )
        == "Notepad:2"
    )
    first_args = {first_name_key: "Notepad", "inputs_json": {"count": 2}}
    first_ctx = context(tool, json.dumps(first_args), call_id="first", session=session)
    first = asyncio.create_task(
        executor.execute(tool=tool, run_ctx=first_ctx, raw_arguments=first_args)
    )
    started = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait(
            [first, started], timeout=1, return_when=asyncio.FIRST_COMPLETED
        )
        if first in done:
            await first  # Surface argument/serialization failures, not a timeout.
        assert entered.is_set()
        duplicate_name_key = "name" if first_name_key == "app_name" else "app_name"
        duplicate_args = {
            duplicate_name_key: "Notepad",
            "inputs": {"count": 2},
        }
        duplicate_ctx = context(
            tool, json.dumps(duplicate_args), call_id="duplicate", session=session
        )
        duplicate_result = await executor.execute(
            tool=tool, run_ctx=duplicate_ctx, raw_arguments=duplicate_args
        )
        assert "already running" in duplicate_result
        distinct_args = {"name": "Notepad", "inputs": {"count": 3}}
        distinct_ctx = context(
            tool, json.dumps(distinct_args), call_id="distinct", session=session
        )
        assert (
            await executor.execute(
                tool=tool, run_ctx=distinct_ctx, raw_arguments=distinct_args
            )
            == "Notepad:3"
        )
        release.set()
        assert await first == "Notepad:2"
        assert effects == [
            ("baseline", "Notepad", 2),
            ("first", "Notepad", 2),
            ("distinct", "Notepad", 3),
        ]
    finally:
        release.set()
        started.cancel()
        await executor.aclose()
        await asyncio.gather(first, started, return_exceptions=True)
