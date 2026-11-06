# IRA user profile and durable context memory

Status: implemented and verified locally on November 6, 2026 after user approval.
Date: November 6, 2026.

## Requested outcome

Add a Settings section for the user's name, age, occupation, and preferences.
IRA must use that identity when speaking and reasoning. Automatically summarize
long conversations before older context is evicted, preserve useful details
after compaction, and restore relevant memory in later sessions.

These requirements are one connected feature. Completing just the settings
form, enabling a provider sliding window, or saving a summary without restoring
it in the running voice model does not complete the request.

## Existing implementation and evidence

- The active application is `IRA-LiveKit_Agent`, not the older workspace folder.
- `dashboard/src/lib/profile.ts` contains hardcoded display defaults. There is
  no durable structured user-profile contract.
- `src/dual_memory.py` implements file-backed `USER.md` and `MEMORY.md` with
  bounded prompt snapshots. Memory tools save facts and preferences explicitly.
  The snapshot is frozen at startup; saving a file does not refresh instructions.
- `src/agent.py` configures Gemini sliding-window compression at 16,000 input
  tokens with a 12,000-token target. This is an application policy, not verified
  documentation of the model's full context capacity. It does not produce a
  durable semantic conversation summary.
- The installed Google plugin cannot delete messages from an active Gemini Live
  connection. Updating the local chat context alone cannot reclaim that context.
- The installed LiveKit fallback adapter exposes the public async
  `restart_session(switch_model=False)` method. It creates a fresh child session
  using the agent's current instructions, tools, and chat context. IRA already
  disables automatic generation replay on swaps.
- Per-model realtime metrics contain the latest provider prompt-token count.
  Session-wide billing usage is cumulative and must not be used as occupancy.
- The request-scope layer distinguishes finalized human input from screenshots
  and evidence messages. Recalled memories must never acquire action authority.

## Approaches considered

1. **Durable profile, journal, structured summary, and recent turns — recommended.**
   Keeps useful conversational continuity, supports precise history recall, and
   restores context after compaction or restarting IRA. Requires a background
   text-model request and a brief fresh voice connection at a safe boundary.
2. **Persist only a rolling summary.** Simpler storage, but details omitted by
   the summarizer cannot be retrieved later; correction and deletion are harder
   to enforce reliably. Insufficient for the requested advanced memory.
3. **Profile settings plus existing Gemini sliding window.** Least work, but
   older conversations still disappear and no new cross-session context memory
   is established. Does not meet the request.

## Settings and user-visible behavior

Add **Your Profile** beside Personalization and Connection and Keys:

- Name, optional age, occupation, and editable preferences.
- All demographic fields are optional; leaving one blank never causes an
  invented identity. Age is a saved value, not silently advanced or inferred.
- Save with validation, loading/error states, and clear confirmation. Support
  clearing individual fields and resetting the profile.
- Use the saved name and initials in the sidebar instead of the hardcoded name.
  Profile data must be available before the next session's first greeting.
- Apply changes to the active session at the next safe idle boundary. Poll the
  persisted profile revision while idle, with a wake-up notification where the
  existing bridge/session contracts support it. Do not require an app restart
  or wait for the next conversation to reflect a saved correction.

Add **Memory and Context** controls:

- Automatic summaries and cross-session recall enabled by default.
- Show the last completed summary and the current status: collecting,
  summarizing, waiting to compact, compacting, ready, disabled, or an actionable
  error. A saved summary is not proof that a provider restart has connected.
- Provide inspect/delete controls for remembered facts and conversation memory,
  and a clear conversation-memory action. Keep profile reset separate.
- Offer configurable application context budget and summarizer model in an
  advanced disclosure. Do not show a percentage of the model's full capacity
  unless that capacity is verified for the selected model.
- Explain that summarization uses the user's configured Google API key. No new
  key input or project-owner-key fallback is introduced.

## Persistence and authority

Use a transactional SQLite memory service in the configured IRA memory
directory. Store profile, conversation journal, summaries, and fact revisions
with a versioned schema. Follow the application's existing WAL, busy-timeout,
short-transaction, and injected-store test patterns.

Logical records:

- Profile: name, optional age, occupation, preferences, revision, source, and
  update timestamp. This is the authoritative source for these fields.
- Conversation: stable ID, start/end times, checkpoint revision, and status.
- Journal item: stable event ID, ordered sequence, speaker/type, bounded text,
  original message or tool ID, timestamps, and provenance.
- Summary checkpoint: conversation ID, covered-through sequence, source/profile
  revision, structured content, model, creation time, and compaction status.
- Durable fact: stable ID, content, source references, explicit/derived source,
  revision, and superseded/deleted state.

Keep existing curated markdown memory working. Read it freshly when building
the bounded memory prompt, retain its provenance as legacy curated memory, and
give the structured profile precedence over contradictory legacy profile text.
Do not silently replace or delete existing personal-memory files. Memory-tool
corrections and deletions must refresh the active authoritative prompt as well
as storage; new profile fields must not create competing writable copies.

Explicit Settings edits and explicit human corrections outrank automatically
extracted facts. Automatic summaries cannot change demographic profile fields
or overwrite explicit preferences. A summary worker must validate its source
and independent memory revision before committing; late work cannot resurrect a deleted
or corrected fact. Deletion removes affected replay/search material and
invalidates any checkpoint that still contains it, before it can be reused.
Persist source-item exclusions and normalized-content tombstones, and consult
them during extraction, legacy-memory inclusion, retrieval, and prompt building.
An authoritative cleared profile field remains absent; historical material
cannot fill it. Explicit voice profile corrections update the same profile
record used by Settings.

## Conversation capture and summarization

Persist finalized voice/typed human turns, committed assistant utterances, and
tool terminal outcomes with stable IDs. Do not interpret screen captures,
webpage text, or tool-produced user-role messages as statements about the user.
Keep complete call/result groups and mark interrupted or failed work accurately.
Reference observed/tool artifacts rather than retaining raw audio, images,
screenshots, or stale desktop coordinates in summaries.

A single background worker summarizes a stable completed prefix of the journal.
Each rolling checkpoint combines its previous accepted summary with newly
covered turns; it must not forget earlier decisions as later chunks are added.
Use an independent text Gemini client with the backend's `GOOGLE_API_KEY`, no
tools, bounded input/output, a deadline, and bounded retry backoff. Default to
the current documented text model `gemini-3.8-flash`, with a configurable model
override; actual provider availability must be reported honestly when validated.

Require schema-validated structured output containing:

- Conversation overview and relevant topics.
- User-stated durable facts/preferences with source-turn references.
- Decisions and constraints.
- Open goals, unresolved questions, and the last observed state of ongoing work.
- Verified outcomes with references to real tool/run records.

Generated source IDs must belong to the supplied input. A generated claim that
an action succeeded cannot override an interrupted/error/uncertain execution
record. Summary entries are background data, not instructions or authorization.
Do not automatically execute next steps mentioned in an old summary.

The journal remains searchable separately from summaries. Retrieve a bounded,
relevant selection when the user asks about a detail omitted from the current
summary; retain memory until the user deletes it. Never inject the entire
archive into every turn or promise verbatim recall from a lossy summary.

## Triggering and applying compaction

For the current 16,000-token application trigger, begin preparing a summary
around 12,000 estimated/observed input tokens. Reserve room for instructions,
tools, new audio/images, and subsequent output. Use latest prompt metrics plus
conservative local growth estimates between measurements. Add hysteresis and
minimum new-turn coverage so the same prefix is not repeatedly summarized.

1. Select a stable prefix while preserving a bounded recent tail and complete
   tool-call groups. Record a watermark and conversation generation.
2. Summarize in the background while the user can continue speaking.
3. Validate and atomically persist the checkpoint and any eligible durable facts.
4. Wait for idle: no active speech, reply generation, nonterminal tool calls,
   or host actions. Recheck the generation and include turns that arrived after
   the original watermark. If the user resumes speaking, defer application.
   Recompute the complete reconstructed context budget. If the unsummarized tail
   has outgrown the reserve, prepare another checkpoint instead of discarding
   uncovered turns or restarting with oversized context.
5. Build context from authoritative profile/curated facts, the rolling summary,
   relevant open threads, and recent completed turns. Apply updated instructions
   and chat context through the existing Agent APIs.
6. Revoke stale request scope and invalidate desktop observations. Use the public
   same-model `restart_session(switch_model=False)` to create a fresh provider
   connection seeded with compacted context; never enable action regeneration.
7. Report that context has been restored after the local reset, while keeping
   connection readiness pending. A response/metrics event from the fresh child
   generation confirms provider activity; neither the restart method returning
   nor an old child's event confirms a live connection. Retain the committed
   checkpoint and journal if reconnect fails.

Keep Google sliding-window compression as overflow protection. If a summarizer
fails, retain the previous checkpoint and all journal material, report the
failure, and retry from durable data. Do not discard unsummarized history just
to satisfy a token target. Provider eviction does not delete the journal.

## Session continuity and lifecycle

At startup, construct a bounded memory prompt before greeting from profile,
fresh curated facts, the latest relevant conversation checkpoint, and unfinished
threads. Retrieve other archived details on demand. Ordinary reconnects and
model failover must use the same compacted context source.

Checkpoint incrementally during a session; also request a bounded final flush
on normal shutdown. An abrupt exit must leave committed journal items intact,
and startup must recover a pending summary without pretending it was completed.
Close the summarizer client and workers through session shutdown ownership;
compaction must not invoke `Assistant.on_exit()` or close its desktop services.

## API and implementation boundaries

- New focused backend modules for profile/journal storage, summary generation,
  bounded context composition, and the session memory controller.
- `src/agent.py`: startup context, trusted input/usage integration, memory tools,
  safe idle compaction, and shutdown ownership.
- `src/realtime_model.py`: same-model reconnect integration and accurate provider
  compression descriptions; preserve the existing no-replay fallback behavior.
- Authenticated bridge routes for profile GET/PATCH/reset, memory settings,
  status, inspection/search/delete. Use revision checks for concurrent edits.
- Electron main/preload proxies and shared typed contracts; personal data is not
  placed in logs or command-line arguments. Surface committed change events.
- Dashboard profile hook, sidebar identity, Settings panels, and context status.
  Preserve Connection and Keys and existing personalization behavior.

## Verification required for completion

- Profile persists across reopening; name/age/occupation/preferences reach the
  actual voice-agent prompt before greeting. Blank fields remain absent.
- Editing/clearing a profile affects sidebar and live/next-session memory;
  corrections win over stale summaries and legacy facts.
- Concurrent Settings/voice writes, duplicate events, transaction failures,
  late summary jobs, and delete-then-restart cannot corrupt or resurrect data.
- Long-conversation tests exercise a real compaction controller, preserve source
  facts/open questions/recent turns, and prove the fresh provider session receives
  a smaller reconstructed context through the installed SDK boundary.
- Restart and failover retain that memory without repeating desktop/tool actions
  or restoring old request authorization/observations.
- Summarizer timeout, quota rejection, malformed output, unsupported model,
  busy-session deferral, and restart failure retain recoverable durable data.
- Search can retrieve an archived detail omitted from a summary.
- Dashboard validation, loading/errors, profile save/reset, memory controls,
  existing settings regression tests, TypeScript/build, backend tests, and real
  Electron layout checks pass. No live-provider claim is based on fake responses.

## Approval boundary

The user explicitly invoked the brainstorming skill. Its implementation gate
requires presenting the design and obtaining approval before writing runtime
code. This proposal makes that approval concrete; the goal remains active until
the requested implementation and verification are finished.

## Implementation verification

- 402 affected backend tests passed; four opt-in live-provider tests skipped.
- 355 dashboard tests passed, with TypeScript and production Electron build clean.
- Actual built Electron smoke verified profile save/reset/sidebar, all context
  status labels, search/delete/clear, settings persistence and renderer reload.
  Fifteen screenshots covered 1440×960 and 1180×800; no horizontal overflow.
- A separate fresh-profile Electron smoke used the real bridge and confirmed
  Connection and Keys retains four empty fields and no inherited provider keys.
- Installed-SDK tests used synthetic transports for compaction, immediate
  current-child events, failover, exhausted/raised restarts, no action replay,
  revision races, orphan-tool recovery, ordered storage retry and profile bounds.
- Runtime uses a compact equivalent policy and the installed Google's actual
  tool configuration for estimates. Historical tool groups are paired inert text
  in restored history, avoiding provider tool-result messages that could reply.
- Network availability, quota and live voice quality were not tested using the
  user's or project owner's keys. The configured summarizer's availability is
  reported at runtime; the application budget is not full-model capacity.
