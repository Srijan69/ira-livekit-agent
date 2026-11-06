# IRA profile and durable context memory implementation plan

> For agentic workers: use superpowers:subagent-driven-development to implement
> the approved tasks continuously, with tests and review at each boundary.

**Goal:** Give IRA editable user identity, automatic semantic context compaction,
and durable recall during long conversations and across sessions.

**Architecture:** One transactional personal-memory store owns structured
profile, settings, journal, facts, and rolling checkpoints. A tools-disabled
background Gemini summarizer prepares checkpoints; a session controller applies
them only at idle through the SDK's public same-model restart. Electron Settings
and voice tools consume the same backend contracts.

**Tech stack:** Python, SQLite, asyncio, google-genai, LiveKit Agents 1.8.8,
Electron, TypeScript, React, pytest, and Vitest.

**Spec:** `docs/superpowers/specs/2026-11-06-profile-and-context-memory-design.md`.

## Global constraints and rulings

- Preserve all existing uncommitted work and Connection and Keys behavior.
- User approved the design and implementation; no repeated implementation gate.
- Use `uv run python -m pytest` for Python tests; use synthetic providers/keys.
- Never use project-owner credentials for a new provider request.
- Actual Gemini context capacity is unverified. Default application budget is
  16,000 tokens; summarize around 12,000, never label this as full-model capacity.
- Canonical profile edits/clears outrank summaries and legacy demographic facts.
- Memory deletion advances an independent revision and excludes original source
  items/content; late summaries cannot restore deleted material.
- Commit checkpoints before pruning local context. Preserve an unsummarized tail
  and completed tool groups. Never regenerate or authorize old actions.
- Ruling: use the public fallback `restart_session(switch_model=False)` API;
  changing Agent activities would trigger desktop cleanup and is unsuitable.
- Ruling: report local context restored separately from provider responsiveness;
  only current-generation events may confirm readiness.
- Do not commit/stage unrelated files or remove existing personal data for tests.

## Task 1: Personal memory storage and authenticated bridge API

**Owner:** storage worker.
**Create:** `src/personal_memory.py`, `src/personal_memory_bridge.py`,
`tests/test_personal_memory.py`, `tests/test_personal_memory_bridge.py`.

**Interfaces (JSON primitives, no datetime/custom objects at the bridge):**

```python
class MemoryConflict(ValueError): pass
class PersonalMemoryStore:
    def __init__(self, path=None): ...
    def get_profile(self) -> dict: ...
    def save_profile(self, patch: dict, *, expected_revision=None, source="settings") -> dict: ...
    def get_settings(self) -> dict: ...
    def save_settings(self, patch: dict, *, expected_revision=None) -> dict: ...
    def memory_revision(self) -> int: ...
    def start_conversation(self, conversation_id=None) -> str: ...
    def append_item(self, conversation_id, item_id, role, text, *, kind="message", trusted=False, tool_call_id=None, tool_name=None, status=None) -> dict: ...
    def journal(self, conversation_id, *, after=0, through=None, limit=1000) -> list[dict]: ...
    def latest_checkpoint(self, conversation_id=None) -> dict | None: ...
    def save_checkpoint(self, conversation_id, *, through_seq, summary, expected_memory_revision, source_item_ids) -> dict: ...
    def list_memories(self, query="", limit=100) -> list[dict]: ...
    def delete_memory(self, memory_id: str) -> bool: ...
    def clear_conversation_memory(self) -> None: ...
    def search(self, query: str, limit=5) -> list[str]: ...
    def context_snapshot(self, budget_chars=16000) -> str: ...
    def add_fact(self, content: str, *, target="memory", source="explicit", source_item_ids=None) -> dict: ...
    def forget(self, query: str) -> bool: ...
    def excluded(self, content: str) -> bool: ...
    def close(self) -> None: ...
def register_personal_memory_routes(app, authorize, *, store=None): ...
```

Profile fields: `name`, `age` (null or integer 0..130), `occupation`,
`preferences`, `revision`, `updated_at`. Blank strings clear fields. Strings are
bounded (name 120, occupation 200, preferences 4000). Defaults are empty/null.
Settings: `enabled=True`, `context_budget=16000`, `summary_trigger=12000`,
`recent_turns=12`, `summarizer_model="gemini-3.8-flash"`, `revision=0`.
Validate budget 4096..128000, trigger >=1024 and <=budget-2048, recent turns 4..40,
and model identifiers bounded to 120 safe printable characters.

Journal rows contain `seq`, `item_id`, `role`, `text`, `kind`, `trusted`,
`tool_call_id`, `tool_name`, `status`; duplicate IDs are idempotent. Checkpoints
contain `id`, `conversation_id`, `through_seq`, `summary`, `memory_revision`,
`created_at`; cumulative checkpoint input includes the previous summary.
Memory records contain `id`, `kind`, `content`, `created_at`, `source`.

Routes: GET/PATCH `/profile`, POST `/profile/reset`, GET/PATCH `/memory/settings`,
GET `/memory`, GET `/memory/status`, DELETE `/memory/{id}`, POST `/memory/clear`.
`GET /memory` returns `{memories:[...]}` and accepts `query`/`limit`.
`GET /memory/status` returns settings, memory revision, and latest checkpoint.
Mutations accept `expected_revision` where applicable; conflict is HTTP409,
invalid input400, all routes require the existing bridge secret. Route methods
run blocking storage in `asyncio.to_thread`.

- [ ] Write and run failing tests for profile roundtrip/clear/age validation,
  revision conflict, journal dedupe, checkpoint roundtrip and stale job rejection.
- [ ] Implement SQLite transactional storage, indexed bounded retrieval,
  profile precedence, independent revisions, deletion exclusions and recovery.
- [ ] Write/run bridge authorization, malformed input, conflict, inspect/delete,
  and reset tests against real aiohttp routes and temporary stores.
- [ ] Verify `uv run python -m pytest tests/test_personal_memory.py tests/test_personal_memory_bridge.py -q`.

## Task 2: Summary generation and safe compaction controller

**Owner:** context worker.
**Create:** `src/context_summary.py`, `src/context_memory.py`,
`tests/test_context_summary.py`, `tests/test_context_memory.py`.

```python
class ContextSummarizer:
    async def summarize(self, *, previous: dict | None, items: list[dict], profile: dict, model: str) -> dict: ...
    async def aclose(self) -> None: ...
class ContextMemoryController:
    def __init__(self, store, summarizer, *, apply_context, is_idle, invalidate, base_tokens=0): ...
    def start(self, conversation_id=None): ...
    def record(self, item_id, role, text, **metadata): ...
    def observe_usage(self, input_tokens: int): ...
    async def tick(self, *, force=False): ...
    async def flush(self): ...
    async def aclose(self): ...
    def status(self) -> dict: ...
```

`apply_context(summary, recent_items, profile)` is an async callback owned by
root integration. `is_idle()` is a synchronous predicate; `invalidate()` revokes
old permissions/evidence. Controller uses store signatures from Task1 and
allows injected fake summarizer/callbacks without LiveKit or network.
Summary schema: `overview` string; `facts`, `decisions`, `open_threads`,
`outcomes`, `questions` lists of objects with `content` and `source_item_ids`.
Profile is authoritative data. All output source IDs must come from supplied
items or the previous accepted summary. Outcome claims require terminal source
tool records. Cap summary size and reject malformed output and unknown IDs.

- [ ] Red/green tests for tools-disabled Google async request using environment
  key, timeout, malformed JSON/schema, source validation and cumulative summary.
- [ ] Red/green tests for real store/controller thresholds, hysteresis,
  single-flight, stable prefix, recent tail and whole tool groups.
- [ ] Keep voice free while summary runs, persist before apply, recheck revisions
  and idle state, preserve arrivals after watermark, re-check reconstructed
  budget, and prepare another prefix if the tail exceeds reserve.
- [ ] Cover busy deferral, failure retry, disabled mode, final bounded flush,
  crash recovery, deletion during summary, and no action replay on compaction.
- [ ] Verify `uv run python -m pytest tests/test_context_summary.py tests/test_context_memory.py -q`.

## Task 3: Profile and Memory Settings interface

**Owner:** UI worker.
**Create:** `dashboard/src/hooks/usePersonalMemory.ts`,
`dashboard/src/hooks/usePersonalMemory.test.tsx`,
`dashboard/src/components/PersonalMemorySettings.tsx`,
`dashboard/src/components/PersonalMemorySettings.test.tsx`.
**Modify:** `dashboard/src/components/Settings.tsx`, its tests,
`dashboard/src/styles/theme.css`, `dashboard/src/components/Sidebar.tsx`,
`dashboard/src/lib/profile.ts`, and `dashboard/src/App.tsx` only for profile flow.

Consumes root-authored shared `PersonalMemoryApi` and `window.ira.personalMemory`.
Keep existing optional connection API and theme tests compatible. New panel
renders Your Profile and Memory and Context tabs; age blank maps to null,
conflict keeps unsaved input, profile names/initials update sidebar, reset clears
canonical fields, memory list supports search/delete/clear and last checkpoint.
Browser preview handles absence of bridge with a clear unavailable message.

- [ ] Red/green tests for optional fields, save/reset, validation, unsaved errors,
  concurrent refresh, sidebar name update, and memory inspect/delete/clear.
- [ ] Implement accessible keyboard tabs and responsive panels matching Settings.
- [ ] Preserve personalization and Connection and Keys behavior.
- [ ] Verify scoped Vitest, TypeScript and build; capture actual Electron layout.

## Task 4: Root integration, review, and end-to-end verification

**Owner:** root.
**Modify:** shared TS types, Electron main/preload/env declarations,
`src/bridge.py`, `src/agent.py`, `src/realtime_model.py` only if required;
**Create:** `src/personal_memory_agent.py`, focused integration tests and probes.

- [ ] Author shared profile/settings/memory APIs to match Task1 primitive JSON.
- [ ] Register authenticated backend routes and Electron proxy/event handling.
- [ ] Attach a persistent-memory facade to existing voice memory tools, preserving
  fake/injected stores and refreshing snapshots after changes.
- [ ] Build startup instructions from canonical profile, filtered legacy memory
  and latest summary. Explicit voice profile correction uses same store.
- [ ] Attach trusted finalized transcript/chat and committed assistant/tool
  events to controller. Record no screenshots as human profile facts.
- [ ] Observe latest model prompt tokens; apply compacted context via
  `await assistant.update_chat_ctx(...)` and public same-model restart at idle.
  Track current-generation events, never enable regeneration, revoke old scope
  and invalidate old desktop evidence. Poll profile/memory revisions at idle.
- [ ] Wire bounded shutdown flush, close workers/client, and reopen recovery.
- [ ] Independently review storage, controller, UI and integrated runtime against
  the approved spec. Resolve correctness/privacy/replay defects and retest.
- [ ] Run backend affected suites, all dashboard tests, TypeScript and production
  build. Use actual SDK fakes and real bridge/Electron probes for profile startup,
  compacted-context restoration, persistence, deletion and existing key isolation.
- [ ] Verify each spec requirement before marking the goal complete; document
  provider verification limits without claiming synthetic tests are live voice.
