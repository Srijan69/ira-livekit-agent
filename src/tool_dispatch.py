"""Hermes-inspired resilient tool dispatch and argument repair engine.

Provides:
- Robust JSON argument repair (markdown fences, trailing commas, single quotes, unquoted keys).
- Parameter alias coercion (e.g. playlist_name -> name, song -> name).
- Fail-soft diagnostic error formatting to prevent abrupt session termination.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from livekit.agents import RunContext, ToolError
from livekit.agents.llm import FunctionTool, RawFunctionTool, ToolFlag
from livekit.agents.llm.tool_context import (
    CONFIRM_DUPLICATE_PARAM,
    RawFunctionToolInfo,
)
from livekit.agents.llm.utils import (
    build_legacy_openai_schema,
    prepare_function_arguments,
    validated_arguments,
)

logger = logging.getLogger(__name__)

# Common parameter aliases across LLM call outputs
DEFAULT_ALIAS_MAP: dict[str, str] = {
    # App launch
    "name": "app_name",
    "app": "app_name",
    "application": "app_name",
    "application_name": "app_name",
    # Spotify / Music
    "playlist_name": "name",
    "playlist_title": "name",
    "playlist": "name",
    "track_name": "name",
    "song_name": "name",
    "song_title": "name",
    "song": "name",
    "track": "name",
    "artist_name": "artist",
    "singer": "artist",
    "command": "action",
    "control": "action",
    "cmd": "action",
    # Browser
    "link": "url",
    "target": "url",
    "address": "url",
    "site": "url",
    "browser_name": "browser",
    # Tasks
    "task": "title",
    "task_title": "title",
    "due_date": "due_at",
    "due": "due_at",
    "id": "task_id",
    # Skills
    "skill": "name",
    "skill_name": "name",
    "procedure": "name",
    "inputs": "inputs_json",
    "args": "inputs_json",
    "parameters": "inputs_json",
    # Memory
    "fact": "text",
    "memory": "text",
    "preference": "text",
    "pref": "preference",
    "note": "text",
    "content": "text",
    "query_text": "query",
    "search": "query",
    "q": "query",
    # Market report / spreadsheet
    "source": "source_url",
    "output": "output_path",
    "file": "output_path",
    "filename": "output_path",
    "file_path": "output_path",
    "filepath": "output_path",
    "report_path": "output_path",
    "data": "data_json",
    "items": "data_json",
    "assets": "data_json",
}


def repair_json_string(text: str) -> str:
    """Clean up common JSON malformations from LLM completions."""
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # Strip trailing commas before closing braces/brackets
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return text


def repair_tool_arguments(
    raw: Any,
    expected_params: set[str] | None = None,
    alias_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Parse and repair tool call arguments with alias coercion.

    Args:
        raw: The raw arguments (dict, JSON string, or None).
        expected_params: Optional set of allowed/expected parameter names.
        alias_map: Optional mapping of alias -> target parameter name.

    Returns:
        Repaired dictionary of arguments.
    """
    if raw is None or raw == "":
        args: dict[str, Any] = {}
    elif isinstance(raw, dict):
        args = dict(raw)
    elif isinstance(raw, str):
        cleaned = repair_json_string(raw)
        try:
            parsed = json.loads(cleaned)
            args = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            # Try single-quote to double-quote replacement
            try:
                fixed = re.sub(r"(?<!\\)'", '"', cleaned)
                parsed = json.loads(fixed)
                args = parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                logger.warning("Could not parse tool call arguments as JSON: %s", raw)
                args = {"raw_input": raw}
    else:
        args = {"raw_input": raw}

    # Apply alias mapping
    active_aliases = dict(DEFAULT_ALIAS_MAP)
    if alias_map:
        active_aliases.update(alias_map)

    coerced: dict[str, Any] = {}
    for key, val in args.items():
        # If the parameter is already expected by the tool, preserve it directly!
        if expected_params and key in expected_params:
            if key in coerced and (
                type(coerced[key]) is not type(val) or coerced[key] != val
            ):
                raise ValueError(f"Conflicting values for parameter `{key}`")
            coerced[key] = val
            continue

        canonical = active_aliases.get(key, key)
        if (
            expected_params
            and key not in expected_params
            and canonical in expected_params
        ):
            if canonical in coerced and (
                type(coerced[canonical]) is not type(val) or coerced[canonical] != val
            ):
                raise ValueError(f"Conflicting values for parameter `{canonical}`")
            coerced[canonical] = val
        elif not expected_params:
            # Without expected_params, only coerce unambiguous aliases (preserve 'name' as-is)
            if key in active_aliases and key != "name":
                coerced[canonical] = val
            else:
                coerced[key] = val
        else:
            coerced[key] = val

    # Special handling for inputs_json in run_skill if passed as dict
    if "inputs_json" in coerced and isinstance(coerced["inputs_json"], dict):
        coerced["inputs_json"] = json.dumps(coerced["inputs_json"], ensure_ascii=False)

    return coerced


class _ArgumentRepairTool(RawFunctionTool):
    """Use the SDK raw boundary while delegating normal Python calls unchanged.

    Provider arguments bind by keyword to avoid confusing dictionary-valued
    positional Python arguments with the raw dispatch payload. The provider
    schema is the original typed schema; only runtime binding metadata changes.
    """

    def __init__(self, original: FunctionTool, aliases: dict[str, str]) -> None:
        self._original = original
        self._aliases = aliases
        schema = build_legacy_openai_schema(original, internally_tagged=True)
        schema.pop("type", None)
        self._expected = set(schema["parameters"].get("properties", {}))
        self._running: dict[tuple[Any, str], dict[str, tuple[Any, RunContext]]] = {}
        self._duplicate_lock = asyncio.Lock()

        async def dispatch(
            *, raw_arguments: dict[str, Any], context: RunContext = None
        ) -> Any:
            canonical = {}
            for key, value in raw_arguments.items():
                target = key if key in self._expected else self._aliases.get(key, key)
                if target not in self._expected:
                    raise ToolError(
                        f"Unknown parameter `{key}` for `{original.info.name}`"
                    )
                if target in canonical and (
                    type(canonical[target]) is not type(value)
                    or canonical[target] != value
                ):
                    raise ToolError(
                        f"Conflicting values for parameter `{target}` in `{original.info.name}`"
                    )
                canonical[target] = value

            inputs_schema = (
                schema["parameters"].get("properties", {}).get("inputs_json", {})
            )
            if inputs_schema.get("type") == "string" and isinstance(
                canonical.get("inputs_json"), dict
            ):
                canonical["inputs_json"] = json.dumps(
                    canonical["inputs_json"], ensure_ascii=False
                )

            # Preserve the original SDK defaults, constraints, type coercion,
            # context injection and Python binding after names have been repaired.
            args, kwargs = prepare_function_arguments(
                fnc=original, json_arguments=canonical, call_ctx=context
            )
            if (
                original.info.on_duplicate == "allow"
                or original.info.duplicate_scope != "name_and_args"
                or context is None
            ):
                return await original(*args, **kwargs)
            return await self._invoke_deduplicated(canonical, context, args, kwargs)

        info = original.info
        super().__init__(
            dispatch,
            RawFunctionToolInfo(
                name=info.name,
                raw_schema=schema,
                flags=info.flags,
                on_duplicate=info.on_duplicate,
                duplicate_scope=info.duplicate_scope,
            ),
        )
        if hasattr(original, "_ira_scope_facade"):
            self._ira_scope_facade = original._ira_scope_facade

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if "raw_arguments" in kwargs:
            return super().__call__(*args, **kwargs)
        return self._original(*args, **kwargs)

    async def _invoke_deduplicated(self, canonical, context, args, kwargs):
        # Raw tools are compared as sent by the SDK. Retain typed comparisons
        # locally as well, including aliases, defaults and coercible values.
        fields = validated_arguments(self._original, canonical)
        fields.pop(CONFIRM_DUPLICATE_PARAM, None)
        try:
            argument_key = json.dumps(fields, sort_keys=True, default=str)
        except (TypeError, ValueError):
            # Typed dictionaries can contain enum keys JSON cannot encode. Like
            # the SDK, fall back to the input, retaining repaired alias names.
            fallback = {
                key: value
                for key, value in canonical.items()
                if key != CONFIRM_DUPLICATE_PARAM
            }
            argument_key = json.dumps(fallback, sort_keys=True, default=str)
        key = (context.session, argument_key)
        call_id = context.function_call.call_id
        task = asyncio.current_task()
        mode = self.info.on_duplicate
        async with self._duplicate_lock:
            running = self._running.get(key, {})
            if running and mode == "replace":
                if ToolFlag.CANCELLABLE not in self.info.flags or any(
                    not ctx.speech_handle.allow_interruptions
                    for _, ctx in running.values()
                ):
                    raise ToolError(
                        f"cannot replace duplicate call of `{self.info.name}`: running call is not cancellable"
                    )
                tasks = [active for active, _ in list(running.values())]
                for active in tasks:
                    active.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            elif running:
                confirmed = canonical.get(CONFIRM_DUPLICATE_PARAM, False)
                if mode == "confirm" and not confirmed:
                    # The executor removes its confirmation flag before binding.
                    try:
                        confirmed = (
                            json.loads(context.function_call.arguments).get(
                                CONFIRM_DUPLICATE_PARAM, False
                            )
                            is True
                        )
                    except (ValueError, TypeError, AttributeError):
                        confirmed = False
                if mode == "reject" or (mode == "confirm" and not confirmed):
                    return f"Same tool `{self.info.name}` is already running. " + (
                        "Re-call with confirm duplicate True only when the user confirms it."
                        if mode == "confirm"
                        else "Do not run a duplicate."
                    )
            self._running.setdefault(key, {})[call_id] = (task, context)
        try:
            return await self._original(*args, **kwargs)
        finally:
            running = self._running.get(key)
            if running is not None:
                running.pop(call_id, None)
                if not running:
                    self._running.pop(key, None)


def repair_function_tool(tool: FunctionTool | RawFunctionTool):
    """Repair supported provider aliases before the original typed validation.

    Existing raw tools and typed tools without supported aliases are unchanged.
    Adapted tools retain canonical provider schemas and execution policy flags;
    direct Python calls keep the original signature's binding behavior.
    """
    if isinstance(tool, RawFunctionTool):
        return tool
    if not isinstance(tool, FunctionTool):
        raise TypeError("Argument repair requires a LiveKit function tool")
    schema = build_legacy_openai_schema(tool, internally_tagged=True)
    expected = set(schema["parameters"].get("properties", {}))
    aliases = {
        alias: target
        for alias, target in DEFAULT_ALIAS_MAP.items()
        if alias not in expected and target in expected
    }
    return _ArgumentRepairTool(tool, aliases) if aliases else tool


def format_tool_error(
    exc: Exception,
    tool_name: str,
    args: dict[str, Any] | None = None,
    task_context: str | None = None,
) -> str:
    """Format an exception into a fail-soft diagnostic error message and record a log file.

    Instead of terminating the Gemini Live WebRTC session with an unhandled
    exception, this records a structured error log in logs/errors/ and returns
    actionable diagnostic feedback to the model to adapt (e.g. falling back to
    capture_screen and mouse tools) and continue the recursive loop.
    """
    err_str = str(exc) or exc.__class__.__name__
    logger.warning("Fail-soft tool error in %s: %s", tool_name, err_str, exc_info=True)

    from error_logger import log_tool_error

    advice = (
        "Fallback: If browser interaction via Selenium failed or an element is obscured/unreachable, "
        "call capture_screen immediately to view the screen and use click_desktop_element or click_mouse "
        "to complete the action on the visible desktop."
    )
    log_file = log_tool_error(
        tool_name,
        exc,
        args=args,
        task_context=task_context,
        recovery_advice=advice,
    )

    log_ref = f"Error log recorded at {log_file}."

    # Specific error guidance
    if isinstance(exc, LookupError):
        return (
            f"Tool '{tool_name}' notice: The requested item was not found ({err_str}). "
            f"Please verify the name or try searching with alternative wording. {log_ref} "
            f"{advice}"
        )
    if isinstance(exc, TimeoutError) or "timed out" in err_str.lower():
        return (
            f"Tool '{tool_name}' timed out waiting for the action ({err_str}). "
            f"Please inspect the screen or retry. {log_ref} "
            f"{advice}"
        )
    if isinstance(exc, ValueError):
        return (
            f"Tool '{tool_name}' received invalid parameters: {err_str}. {log_ref} "
            f"Please verify parameter values and retry, or use capture_screen to inspect the target."
        )
    return f"Tool '{tool_name}' encountered an issue: {err_str}. {log_ref} {advice}"
