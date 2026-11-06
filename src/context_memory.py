"""Journal-first context compaction, independent of the voice SDK/provider."""

from __future__ import annotations

import asyncio
import copy
import inspect
import time
from collections.abc import Callable

from context_summary import (
    TERMINAL_STATUSES,
    SummaryError,
    estimate_tokens,
    summary_source_ids,
    validate_summary,
)


class ContextApplyDeferred(Exception):  # noqa: N818 - a deferral, not a failure
    """The session resumed input; retain the checkpoint and retry at idle."""


def _tool_spans(items: list[dict]) -> list[tuple[int, int, bool]]:
    groups: dict[str, list[tuple[int, dict]]] = {}
    for index, row in enumerate(items):
        group_id = row.get("tool_call_id")
        if not group_id and row.get("kind") in {"tool_call", "function_call"}:
            group_id = "uncorrelated:" + row["item_id"]
        if group_id:
            groups.setdefault(group_id, []).append((index, row))
    return [
        (
            rows[0][0],
            rows[-1][0],
            any(row.get("status") in TERMINAL_STATUSES for _, row in rows),
        )
        for rows in groups.values()
    ]


def _safe_boundary(items: list[dict], boundary: int) -> int:
    """A prefix never includes part of a call/result pair or an unfinished call."""
    spans = _tool_spans(items)
    while True:
        adjusted = boundary
        for start, end, complete in spans:
            if start < adjusted and (end >= adjusted or not complete):
                adjusted = min(adjusted, start)
        if adjusted == boundary:
            return adjusted
        boundary = adjusted


def _tail_boundary(items: list[dict], recent_turns: int) -> int:
    messages = [
        index
        for index, row in enumerate(items)
        if row.get("role") in {"user", "assistant"}
        and row.get("kind", "message") in {"message", "transcript"}
    ]
    if len(messages) <= recent_turns:
        return 0
    return _safe_boundary(items, messages[-recent_turns])


class ContextMemoryController:
    def __init__(
        self,
        store,
        summarizer,
        *,
        apply_context,
        is_idle,
        invalidate,
        base_tokens: int | Callable[[], int] = 0,
        retry_delay_seconds: float = 5.0,
        flush_timeout_seconds: float = 8.0,
    ):
        if retry_delay_seconds < 0 or flush_timeout_seconds <= 0:
            raise ValueError("Invalid controller retry or flush deadline")
        self.store = store
        self.summarizer = summarizer
        self.apply_context = apply_context
        self.is_idle = is_idle
        self.invalidate = invalidate
        self.base_tokens = base_tokens
        self.retry_delay_seconds = retry_delay_seconds
        self.flush_timeout_seconds = flush_timeout_seconds
        self.conversation_id: str | None = None
        self._items: list[dict] = []
        self._checkpoint: dict | None = None
        self._covered = 0
        self._pending: dict | None = None
        self._memory_revision = 0
        self._generation = 0
        self._arrival_version = 0
        self._task: asyncio.Task | None = None
        self._operation_lock = asyncio.Lock()
        self._journal_lock = asyncio.Lock()
        self._latest_usage: int | None = None
        self._growth = 0
        self._reconstructed_tokens = 0
        self._state = "collecting"
        self._last_error: str | None = None
        self._retry_at = 0.0
        self._local_context_restored = False
        self._provider_ready = False
        self._provider_generation = 0
        self._published_status: dict | None = None
        self._journal_error = False
        self._journal_prior_state = "collecting"
        self._journal_prior_error: str | None = None
        self._closed = False

    def _read_journal(self, *, after=0) -> list[dict]:
        rows: list[dict] = []
        while True:
            page = self.store.journal(self.conversation_id, after=after, limit=1000)
            rows.extend(page)
            if len(page) < 1000:
                return rows
            after = page[-1]["seq"]

    def start(self, conversation_id=None):
        """Recover accepted memory and durable uncheckpointed items synchronously.

        Call before attaching the session's event handlers. A new conversation
        starts with the latest accepted cross-session summary as background data.
        """
        if self._closed or (self._task is not None and not self._task.done()):
            raise RuntimeError("Controller is closed or already summarizing")
        self.conversation_id = self.store.start_conversation(conversation_id)
        self._generation += 1
        self._memory_revision = self.store.memory_revision()
        self._checkpoint = self.store.latest_checkpoint(self.conversation_id)
        if self._checkpoint is None:
            self._checkpoint = self.store.latest_checkpoint()
        self._covered = (
            self._checkpoint["through_seq"]
            if self._checkpoint
            and self._checkpoint["conversation_id"] == self.conversation_id
            else 0
        )
        self._items = self._read_journal(after=self._covered)
        self._pending = copy.deepcopy(self._checkpoint)
        if self._pending and self._pending["conversation_id"] != self.conversation_id:
            self._pending["through_seq"] = 0
        self._state = "waiting_to_compact" if self._pending else "collecting"
        if not self.store.get_settings()["enabled"]:
            self._state = "disabled"
        self._last_error = None
        self._local_context_restored = False
        self._provider_ready = False
        self._latest_usage = None
        self._growth = 0
        self._reconstructed_tokens = 0
        return self.conversation_id

    async def record(self, item_id, role, text, **metadata):
        """Await this before accepting an event; duplicate IDs remain idempotent."""
        if self.conversation_id is None or self._closed:
            raise RuntimeError("Controller has not started or is closed")
        async with self._journal_lock:

            def append_allowed():
                row = self.store.append_item(
                    self.conversation_id, item_id, role, text, **metadata
                )
                # The storage API returns an idempotent row even when a source
                # was deleted. Only a row in the filtered journal is usable.
                allowed = self.store.journal(
                    self.conversation_id,
                    after=row["seq"] - 1,
                    through=row["seq"],
                    limit=1,
                )
                return allowed[0] if allowed else None

            row = await asyncio.to_thread(append_allowed)
            if (
                row
                and row["seq"] > self._covered
                and not any(item["item_id"] == row["item_id"] for item in self._items)
            ):
                self._items.append(row)
                self._items.sort(key=lambda item: item["seq"])
                self._growth += estimate_tokens(row) + 16
                self._arrival_version += 1
            return row

    def observe_usage(self, input_tokens: int):
        """Observe the latest prompt metric, never cumulative billing usage."""
        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens < 0
        ):
            return
        self._latest_usage = input_tokens
        self._growth = 0

    def _base(self) -> int:
        value = self.base_tokens() if callable(self.base_tokens) else self.base_tokens
        return max(0, int(value))

    def _tail(self, through=None):
        watermark = self._covered if through is None else through
        return [row for row in self._items if row["seq"] > watermark]

    def _input_tokens(self) -> int:
        local = self._reconstructed_tokens + self._growth
        if not self._reconstructed_tokens:
            local = self._base() + estimate_tokens(self._tail())
        return max(local, (self._latest_usage or 0) + self._growth)

    def status(self) -> dict:
        return {
            "state": self._state,
            "conversation_id": self.conversation_id,
            "through_seq": self._covered,
            "input_tokens": self._input_tokens(),
            "local_context_restored": self._local_context_restored,
            "provider_ready": self._provider_ready,
            "provider_generation": self._provider_generation,
            "last_error": self._last_error,
        }

    def mark_provider_ready(self, generation=None) -> bool:
        """Root calls only on a verified event from the fresh child generation."""
        if (
            self._journal_error
            or self._state == "disabled"
            or (not self._local_context_restored and self._state != "compacting")
            or (generation is not None and generation != self._provider_generation)
        ):
            return False
        self._provider_ready = True
        if self._local_context_restored and self._state in {"restored", "ready"}:
            self._state = "ready"
        return True

    def provider_reconnecting(self) -> int:
        """Invalidate provider evidence when root observes an active child change.

        Controller-driven compaction starts its generation before the callback;
        a child created inside that callback belongs to the same generation.
        Automatic fallback/direct refresh starts a new generation here instead.
        """
        self._provider_ready = False
        if self._state != "compacting":
            self._provider_generation += 1
        if self._state == "ready":
            self._state = "restored"
        return self._provider_generation

    def mark_context_restored(self, generation=None) -> bool:
        """Root calls after successful startup/direct local context seeding.

        This confirms local instructions/history only. Provider readiness still
        requires a separate verified current-generation provider event.
        """
        if generation is not None and generation != self._provider_generation:
            return False
        self._local_context_restored = True
        if self._state in {"collecting", "restored", "ready"}:
            self._state = "ready" if self._provider_ready else "restored"
        return True

    def provider_failed(
        self, error="Voice connection failed; journal retained"
    ) -> None:
        """Root reports a fatal provider failure, without storing raw exceptions."""
        safe_messages = {
            "Voice connection failed; journal retained",
            "Voice restart failed; journal retained",
        }
        self._provider_ready = False
        self._local_context_restored = False
        self._state = "error"
        self._last_error = (
            error
            if isinstance(error, str) and error in safe_messages
            else "Voice connection failed; journal retained"
        )
        self._retry_at = time.monotonic() + self.retry_delay_seconds

    def journal_failed(self) -> None:
        """Root reports an ordered writer retry, without losing another error."""
        message = "Conversation storage unavailable; waiting to save captured turns"
        if not self._journal_error or (
            self._state == "error" and self._last_error != message
        ):
            self._journal_prior_state = self._state
            self._journal_prior_error = self._last_error
        self._journal_error = True
        self._provider_ready = False
        self._state = "error"
        self._last_error = message

    def journal_recovered(self) -> None:
        """Clear this writer error only; preserve summary/provider failures."""
        if not self._journal_error:
            return
        self._journal_error = False
        if (
            self._last_error
            != "Conversation storage unavailable; waiting to save captured turns"
        ):
            return
        if self._journal_prior_error:
            self._state = self._journal_prior_state
            self._last_error = self._journal_prior_error
            return
        self._last_error = None
        if self._state == "disabled" or self._journal_prior_state == "disabled":
            self._state = "disabled"
        elif self._pending:
            self._state = "waiting_to_compact"
        elif self._task is not None and not self._task.done():
            self._state = "summarizing"
        elif self._local_context_restored:
            self._state = "restored"
        else:
            self._state = "collecting"

    async def _publish_status(self):
        current = self.status()
        if current != self._published_status and self.conversation_id is not None:
            await asyncio.to_thread(
                self.store.set_context_status, self.conversation_id, current
            )
            self._published_status = current

    async def _refresh_revision(self):
        revision = await asyncio.to_thread(self.store.memory_revision)
        if revision == self._memory_revision:
            return
        self._memory_revision = revision
        self._generation += 1
        self._checkpoint = await asyncio.to_thread(
            self.store.latest_checkpoint, self.conversation_id
        )
        if self._checkpoint is None:
            self._checkpoint = await asyncio.to_thread(self.store.latest_checkpoint)
        self._covered = (
            self._checkpoint["through_seq"]
            if self._checkpoint
            and self._checkpoint["conversation_id"] == self.conversation_id
            else 0
        )
        async with self._journal_lock:
            self._items = await asyncio.to_thread(
                self._read_journal, after=self._covered
            )
        self._pending = copy.deepcopy(self._checkpoint)
        if self._pending:
            self._pending["through_seq"] = self._covered
        else:
            # Deletion itself is already durable. An empty reset removes stale
            # provider memory without recreating a checkpoint for deleted data.
            self._pending = {
                "id": None,
                "conversation_id": self.conversation_id,
                "through_seq": 0,
                "memory_revision": revision,
                "summary": {
                    "overview": "",
                    "facts": [],
                    "decisions": [],
                    "open_threads": [],
                    "outcomes": [],
                    "questions": [],
                },
            }
        self._latest_usage = None
        self._growth = 0
        self._reconstructed_tokens = 0
        self._provider_ready = False
        self._local_context_restored = False
        self._state = "waiting_to_compact"

    def _prefix(self, settings, profile, *, final=False, pressure=False):
        tail = self._tail()
        boundary = (
            _safe_boundary(tail, len(tail))
            if final
            else _tail_boundary(tail, 2 if pressure else settings["recent_turns"])
        )
        previous = self._checkpoint["summary"] if self._checkpoint else None
        budget = getattr(self.summarizer, "max_input_tokens", 24000)
        # Reserve the actual summarizer's instructions/config overhead. Never
        # truncate a message: move the boundary to an earlier whole tool group.
        while (
            boundary > 0
            and estimate_tokens(
                {"previous": previous, "items": tail[:boundary], "profile": profile}
            )
            + 1000
            > budget
        ):
            boundary = _safe_boundary(tail, boundary - 1)
        return copy.deepcopy(tail[:boundary])

    def _launch(self, prefix, profile, settings):
        previous = copy.deepcopy(
            self._checkpoint["summary"] if self._checkpoint else None
        )
        self._state = "summarizing"
        self._last_error = None
        self._task = asyncio.create_task(
            self._prepare(
                prefix,
                previous,
                profile,
                settings,
                self._memory_revision,
                self._generation,
            ),
            name="ira-context-summary",
        )

    async def _prepare(self, prefix, previous, profile, settings, revision, generation):
        try:
            generated = await self.summarizer.summarize(
                previous=previous,
                items=prefix,
                profile=profile,
                model=settings["summarizer_model"],
            )
            accepted = validate_summary(
                generated,
                previous=previous,
                items=prefix,
                max_summary_tokens=getattr(self.summarizer, "max_summary_tokens", 2000),
            )
            if generation != self._generation or revision != await asyncio.to_thread(
                self.store.memory_revision
            ):
                raise SummaryError(
                    "Memory changed during summary; stale result discarded"
                )
            if not (await asyncio.to_thread(self.store.get_settings))["enabled"]:
                self._state = "disabled"
                return
            checkpoint = await asyncio.to_thread(
                self.store.save_checkpoint,
                self.conversation_id,
                through_seq=prefix[-1]["seq"],
                summary=accepted,
                expected_memory_revision=revision,
                source_item_ids=sorted(
                    summary_source_ids(previous) | {row["item_id"] for row in prefix}
                ),
            )
            if generation != self._generation:
                return
            self._checkpoint = checkpoint
            self._covered = checkpoint["through_seq"]
            # The archive remains in SQLite; resident state needs only uncovered
            # rows after the checkpoint has committed successfully.
            self._items = [row for row in self._items if row["seq"] > self._covered]
            self._pending = checkpoint
            self._state = "waiting_to_compact"
            self._last_error = None
        except asyncio.CancelledError:
            self._state = "error"
            self._last_error = "Summary cancelled; journal retained for recovery"
            raise
        except Exception:
            self._state = "error"
            self._last_error = (
                "Summary failed or memory changed; journal retained for retry"
            )
            self._retry_at = time.monotonic() + self.retry_delay_seconds

    async def _apply_pending(self, settings, profile):
        checkpoint = self._pending
        tail = copy.deepcopy(self._tail(checkpoint["through_seq"]))
        reconstructed = (
            self._base()
            + estimate_tokens(profile)
            + estimate_tokens(checkpoint["summary"])
            + estimate_tokens(tail)
        )
        limit = settings["context_budget"] - 2048
        if reconstructed > limit:
            prefix = self._prefix(settings, profile, pressure=True)
            if prefix:
                self._launch(prefix, profile, settings)
            else:
                self._state = "error"
                self._last_error = "Recent context exceeds the reserve; all unsummarized journal items retained"
            return
        if not self.is_idle() or any(
            not complete for _, _, complete in _tool_spans(tail)
        ):
            self._state = "waiting_to_compact"
            return
        # Check after awaited reads and again immediately before the callback.
        revision = await asyncio.to_thread(self.store.memory_revision)
        latest_profile = await asyncio.to_thread(self.store.get_profile)
        if revision != self._memory_revision:
            await self._refresh_revision()
            return
        tail = copy.deepcopy(self._tail(checkpoint["through_seq"]))
        reconstructed = (
            self._base()
            + estimate_tokens(latest_profile)
            + estimate_tokens(checkpoint["summary"])
            + estimate_tokens(tail)
        )
        if (
            reconstructed > limit
            or not self.is_idle()
            or any(not complete for _, _, complete in _tool_spans(tail))
        ):
            self._state = "waiting_to_compact"
            return
        arrival_version = self._arrival_version
        self._state = "compacting"
        self._provider_ready = False
        self._provider_generation += 1
        self._local_context_restored = False
        try:
            invalidated = self.invalidate()
            if inspect.isawaitable(invalidated):
                await invalidated
            if not self.is_idle():
                self._state = "waiting_to_compact"
                return
            await self.apply_context(checkpoint["summary"], tail, latest_profile)
        except ContextApplyDeferred:
            self._state = "waiting_to_compact"
            self._last_error = None
            self._provider_ready = False
            self._local_context_restored = False
            self._retry_at = 0.0
            return
        except Exception:
            self._state = "error"
            self._last_error = (
                "Context restore failed; committed checkpoint retained for retry"
            )
            self._retry_at = time.monotonic() + self.retry_delay_seconds
            return
        if self._memory_revision != await asyncio.to_thread(self.store.memory_revision):
            await self._refresh_revision()
            self._state = "error"
            self._last_error = (
                "Memory changed during context restore; rebuild from current memory"
            )
            return
        self._local_context_restored = True
        self._last_error = None
        self._latest_usage = None
        self._growth = 0
        self._reconstructed_tokens = reconstructed
        # Any late arrival remains journaled and will be included on the next
        # safe application; never overwrite the mutable tail with an old copy.
        self._pending = None if arrival_version == self._arrival_version else checkpoint
        self._state = (
            ("ready" if self._provider_ready else "restored")
            if self._pending is None
            else "waiting_to_compact"
        )

    async def tick(self, *, force=False):
        """Schedule background work or apply an accepted checkpoint at idle."""
        if self.conversation_id is None or self._closed:
            return
        async with self._operation_lock:
            settings = await asyncio.to_thread(self.store.get_settings)
            await self._refresh_revision()
            if not settings["enabled"]:
                self._state = "disabled"
                await self._publish_status()
                return
            if self._task is not None:
                if not self._task.done():
                    await self._publish_status()
                    return
                self._task = None
                if self._state == "error":
                    await self._publish_status()
                    return
            if not force and time.monotonic() < self._retry_at:
                return
            profile = await asyncio.to_thread(self.store.get_profile)
            if self._pending:
                await self._apply_pending(settings, profile)
            elif force or self._input_tokens() >= settings["summary_trigger"]:
                prefix = self._prefix(settings, profile)
                new_messages = sum(
                    row.get("kind", "message") in {"message", "transcript"}
                    for row in prefix
                )
                if prefix and (force or new_messages >= 2):
                    self._launch(prefix, profile, settings)
            elif self._state == "disabled":
                self._state = "collecting"
            await self._publish_status()

    async def flush(self):
        """Bounded final checkpoint; incomplete groups remain recoverable raw data.

        Shutdown checkpointing does not restart a voice provider or require an
        idle callback. The next startup can restore the durable result.
        """
        if self.conversation_id is None or self._closed:
            return

        async def finish():
            async with self._operation_lock:
                settings = await asyncio.to_thread(self.store.get_settings)
                if not settings["enabled"]:
                    self._state = "disabled"
                    await self._publish_status()
                    return
                if self._task is not None:
                    await self._task
                    self._task = None
                await self._refresh_revision()
                profile = await asyncio.to_thread(self.store.get_profile)
                prefix = self._prefix(settings, profile, final=True)
                if prefix:
                    self._launch(prefix, profile, settings)
                    await self._task
                    self._task = None

        try:
            await asyncio.wait_for(finish(), timeout=self.flush_timeout_seconds)
        except (TimeoutError, asyncio.CancelledError):
            self._state = "error"
            self._last_error = (
                "Final summary deadline reached; journal retained for recovery"
            )
            await self._publish_status()

    async def aclose(self):
        if self._closed:
            return
        await self.flush()
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        try:
            await asyncio.wait_for(
                self.summarizer.aclose(), timeout=self.flush_timeout_seconds
            )
        except TimeoutError:
            self._last_error = "Summarizer close deadline reached; journal retained"
