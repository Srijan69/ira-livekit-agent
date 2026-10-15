"""Small, failure-proof terminal status reporting for realtime sessions."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone


class SessionStatusReporter:
    """Emit compact lifecycle lines without ever interrupting the agent."""

    def __init__(
        self,
        run_id: str | None = None,
        sink: Callable[[str], None] | None = None,
        clock: Callable[[], float] | None = None,
        label: str = "session",
        started_at: float | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._sink = sink or print
        self._clock = clock or time.monotonic
        self._started_at = self._clock() if started_at is None else started_at
        self._label = label

    def emit(self, state: str, detail: str = "") -> None:
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
                f"{state.upper()} +{elapsed_ms}ms{suffix}"
            )
        except Exception:
            return

    def child_run(self, label: str) -> SessionStatusReporter:
        return SessionStatusReporter(
            run_id=self.run_id,
            sink=self._sink,
            clock=self._clock,
            label=label,
            started_at=self._started_at,
        )
