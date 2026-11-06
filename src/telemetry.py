"""Small, failure-proof terminal status reporting for realtime sessions."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class SessionStatusReporter:
    """Emit compact lifecycle lines without ever interrupting the agent.

    Two audiences, one source of truth:

    - ``sink`` gets a formatted line for the terminal. These are the
      diagnostics this project already relies on, and their shape is
      unchanged.
    - ``observer`` gets the same event as a dict, for the desktop dashboard's
      orb. It needed structure rather than a string to match on, and inventing
      a parallel state system alongside this one would have meant two
      descriptions of the session that could disagree.

    Neither may raise into the agent. A stale orb or a missing log line is
    survivable; a dropped turn on a voice call is not.
    """

    def __init__(
        self,
        run_id: str | None = None,
        sink: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
        label: str = "session",
        started_at: float | None = None,
        observer: Callable[[dict[str, Any]], None] | None = None,
        call_id: str | None = None,
        tool_name: str | None = None,
        source: str | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._sink = sink or print
        self._clock = clock or time.monotonic
        self._started_at = self._clock() if started_at is None else started_at
        self._label = label
        self._observer = observer
        self._call_id = call_id
        self._tool_name = tool_name
        self._source = source

    def emit(
        self,
        state: str,
        detail: str = "",
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        source: str | None = None,
        skill_run_id: str | None = None,
        skill_slug: str | None = None,
        skill_version: int | None = None,
        step_index: int | None = None,
        step_count: int | None = None,
        step_tool: str | None = None,
        step_outcome: str | None = None,
        outcome: str | None = None,
    ) -> None:
        # Upper-cased once, here, because the orb matches on these names and a
        # lower-case "running" from one call site would silently miss.
        state = state.upper()
        try:
            elapsed_ms = max(0, int((self._clock() - self._started_at) * 1000))
            timestamp = (
                datetime.now(timezone.utc)
                .astimezone()
                .isoformat(timespec="milliseconds")
            )
            suffix = f" {detail}" if detail else ""
            self._sink(
                f"[{timestamp}] [{self.run_id}] [{self._label}] "
                f"{state} +{elapsed_ms}ms{suffix}"
            )
        except Exception:
            pass

        if self._observer is None:
            return

        # Separate try block: the observer publishes over a network and so has
        # more ways to fail than `print`. A failure there must not cost the
        # terminal line, nor the other way round.
        try:
            event = {
                "run_id": self.run_id,
                "label": self._label,
                "state": state,
                "detail": detail,
                "at": time.time() * 1000,
            }
            for key, value in (
                ("call_id", call_id or self._call_id),
                ("tool_name", tool_name or self._tool_name),
                ("source", source or self._source),
                ("skill_run_id", skill_run_id),
                ("skill_slug", skill_slug),
                ("skill_version", skill_version),
                ("step_index", step_index),
                ("step_count", step_count),
                ("step_tool", step_tool),
                ("step_outcome", step_outcome),
                ("outcome", outcome),
            ):
                if value is not None and value != "":
                    event[key] = value
            self._observer(event)
        except Exception:
            return

    def for_tool(self, context: Any) -> SessionStatusReporter:
        """Bind internal progress to one call without mutating the shared reporter.

        ``RunContext.function_call`` is public LiveKit metadata. Standalone tool
        tests pass None and retain their existing terminal-only behavior.
        """
        try:
            call = getattr(context, "function_call", None)
            call_id = getattr(call, "call_id", None)
            name = getattr(call, "name", None)
        except Exception:
            return self
        if not isinstance(call_id, str) or not call_id:
            return self
        return SessionStatusReporter(
            run_id=self.run_id,
            sink=self._sink,
            clock=self._clock,
            label=self._label,
            started_at=self._started_at,
            observer=self._observer,
            call_id=call_id,
            tool_name=name if isinstance(name, str) else None,
            source="tool",
        )

    def child_run(self, label: str) -> SessionStatusReporter:
        return SessionStatusReporter(
            run_id=self.run_id,
            sink=self._sink,
            clock=self._clock,
            label=label,
            started_at=self._started_at,
            observer=self._observer,
            call_id=self._call_id,
            tool_name=self._tool_name,
            source=self._source,
        )
