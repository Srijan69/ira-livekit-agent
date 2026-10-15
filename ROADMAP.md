# IRA — the road to a Friday-level assistant

Ideas for turning IRA from a voice agent into something that feels like FRIDAY.
Most of this isn't built yet. Pick what you want and I'll implement it.

## Where she stands today

The hard part is already done. IRA is true speech-to-speech — one Gemini Live
model takes audio in and emits audio out, with no transcription step in the
middle. That's what makes her feel like a person rather than a phone menu, and
it's the constraint everything below has to respect.

What she has: a voice, a persona, durable memory, a browser she drives by
number, `open_app`, and a greeting when you connect.

What separates that from FRIDAY is not intelligence — the model is already
smart enough. It's three things:

| FRIDAY does | IRA today |
|---|---|
| Remembers everything, forever | ✅ Remembers, in SQLite, across sessions |
| Is always there, no starting her | You run a console command |
| Speaks up when something matters | Only answers when spoken to |
| Sees what he sees, acts on everything | Blind; opens apps and drives a browser |

The rest of this document is how to close the remaining gaps, and what to build
after.

> **A note on verification.** LiveKit's SDK moves fast and I don't trust my own
> memory of its API. Items marked **Verify first** depend on something I have
> not confirmed against current docs or a live test — the approach is sound but
> the specific API needs checking before I write code against it. Unmarked
> items use paths already exercised in this repo.

---

## Tier 1 — the three that change how she feels

These are worth doing before anything else. None of them adds a capability so
much as it removes a reason she feels like software.

### 1. Memory across sessions — ✅ **built**

**What.** She remembers the boss's name, preferences, what you were working on
yesterday, that you hate being asked to confirm things twice.

**Why it mattered most.** Every conversation used to be with a stranger who
happened to have the same voice. Continuity is the single biggest gap between
"voice interface" and "assistant". Everything else on this list is more
impressive; this one was more missed.

**What shipped**, in [`src/memory.py`](src/memory.py) — see the README's
*Memory* section for the full behavior:

- `remember`, `recall` and `forget` function tools, so she decides what's worth
  keeping mid-conversation rather than you managing a file.
- SQLite in the project directory, with FTS5 + BM25 ranked search and a `LIKE`
  fallback for interpreters built without FTS5.
- Seeding via the instructions at session start rather than
  `update_chat_ctx()` — same effect, one less API surface to depend on, and it
  composes with the persona prompt instead of racing it.

**The trap, and how it was handled.** Loading everything every session is the
context bloat that makes voice agents feel slow, and latency is the one thing a
voice agent cannot spend. So `seed()` and `recall()` are deliberately separate
paths: seed caps at 12 facts ranked by proven usefulness, and everything else is
retrieved on demand. That split is the whole design.

**Still open:** the database lives on ephemeral storage under a LiveKit Cloud
deploy, so `IRA_MEMORY_DB` has to point at a mounted volume there or memory
silently resets on every deploy.

### 2. Always there — wake word and persistence

**What.** You say "IRA" into the room and she answers. No terminal, no command.
She's running from boot, in the tray.

**How.** A small always-on process listening locally for the wake word
(openWakeWord is free and offline; Picovoice Porcupine is more accurate with a
free personal tier). On detection it connects to the LiveKit room and starts a
session; on silence it disconnects.

Worth noting: this sits *outside* the agent entirely. It's a launcher, not an
agent change, so it can't break the realtime path. That also makes it low-risk
to try.

**The trap.** False triggers. A wake word that fires on the TV is an annoyance
for most assistants; for one that can control your desktop it's the security
problem described at the bottom of this document. Build this and voice
verification in the same breath, or keep her tools harmless until you have.

**Effort:** medium.

### 3. Proactivity — she speaks first

**What.** "Boss, that build finished — three tests failed." Unprompted. This is
the most FRIDAY behavior on the list.

**How.** Two independent halves, and the second is the useful one:

- **Model-side:** Gemini's native-audio models support a proactivity setting,
  where the model decides whether a given utterance deserves a response at all
  (so she can ignore you talking to someone else). **Now confirmed reachable** —
  the plugin's `_RealtimeOptions` carries `proactivity` and
  `enable_affective_dialog` as constructor kwargs on `RealtimeModel`, right
  alongside the `realtime_input_config` the latency work already sets, so wiring
  them is the same one-line shape.
- **Agent-side:** a background task watching whatever you care about — a build,
  a file, the clock, your battery — that calls
  `session.generate_reply(instructions=...)` when a condition fires. That's the
  exact call that already produces her greeting, so it's a known-good path.

The agent-side half needs no new API and does most of the work.

**Deliberately not enabled yet.** Both model-side flags change how she decides
to speak, and the greeting plus three of the live evals depend on her speaking
predictably. Turning them on wants its own test pass rather than a ride-along on
unrelated work — `proactivity` in particular could suppress the opening greeting
outright, which is exactly the kind of change that should fail a test rather than
surprise you on a call.

**The trap.** An assistant that interrupts badly is worse than one that never
interrupts. Rate-limit it, give it quiet hours, and make the bar for speaking
genuinely high.

**Effort:** low for the agent-side half. **Best effort-to-impact ratio here.**

---

## Tier 2 — reach

Once she remembers, persists, and speaks up, these make her useful.

### 4. Vision — she sees your screen

**What.** "What's this error?" — without you reading the stack trace aloud.

**How.** Publish the screen as a video track into the LiveKit room; Gemini Live
accepts video alongside audio. **Verify first** — video input through the
plugin's `RealtimeModel` is the piece I'd want to confirm before committing.

**The trap.** Streaming your screen continuously into a realtime model costs
money and adds latency to every turn. Gate it: publish only when she's asked to
look ("take a look at this"), then stop.

**Effort:** medium-high. **The biggest jump in what she can actually help with.**

### 5. Real desktop control

`open_app` is honestly a hack — it types into the Start menu and hopes. It
works, but it's nondeterministic, and it's one verb.

Worth adding, roughly in order of usefulness per hour of work:

- **Clipboard** read and write. "What did I just copy?" / "Put that on my clipboard."
- **Media keys** — play, pause, skip, volume. Trivial, and you'll use it daily.
- **Window management** — focus, minimize, arrange (via `pygetwindow`).
- **Screenshot** — cheap on its own, and the input to #4.
- **Launch by path or `start`** rather than Start-menu search, so opening an app
  is deterministic. This is a robustness fix to what exists, not a new feature.
- **Dictation** — type what I say into the focused window.

**The trap.** Every tool you add is more context in every request and one more
thing a misheard sentence can trigger. Past roughly a handful of tools, split
them out (#9) rather than growing the list.

**Effort:** low each.

### 6. System awareness

Battery, CPU, disk, network, which window has focus, what's playing, the time.
`psutil` covers most of it in an afternoon.

Small on its own, but it's what makes her feel situated rather than
teleconferenced in — and combined with #3 it's where "battery's at eight
percent, boss" comes from.

**Effort:** low. **Punches well above its weight.**

### 7. Web search — partly covered by browsing

She can now reach the live web by driving Chrome (README, *Web browsing*), so
"look up X" works today: navigate to a search engine, type, submit, read.

What's still missing is *fast* search. Driving a browser costs seconds and a
visible window, which is the right shape for "go to this site and do something"
and the wrong shape for "what's the exchange rate". For that, either Gemini's
built-in Google Search grounding (**Verify first** — whether the realtime plugin
exposes grounding) or a plain `search_web` function tool against a search API.

**Effort:** low to medium.

### 8. Calendar, email, messages

"What's my day look like?" is the most assistant-like thing an assistant does.
Google Calendar and Gmail APIs; the work is almost entirely OAuth, not logic.

**Effort:** medium.

---

## Tier 3 — architecture and presence

### 9. Split her into multiple agents

Not a feature — the thing that keeps the features above from making her slow.

A single agent carrying every tool and every instruction gets worse at choosing
tools and slower at every turn. LiveKit supports handoffs (one agent passes
control to another) and tasks (tightly-scoped one-shot prompts). The shape: a
lean IRA who greets, chats, and routes, handing off to a desktop agent, a
research agent, a home agent — each with a short prompt and a few tools.

Both this project's `AGENTS.md` and LiveKit's own guidance call for this
explicitly. **Do it before the tool list gets long, not after** — retrofitting
is much more work than starting split.

**This is now the most overdue item on the list.** The tool count went from one
to eleven when browsing and memory landed, and all eleven are on a single agent
with one prompt. That was a deliberate call rather than an oversight: IRA is an
always-on general assistant with no distinct conversational phases, and a
handoff needs a phase boundary to trigger on. A "browsing mode" she hands off to
would mean she can't answer an unrelated question without handing back, which
trades a real cost in conversational feel for a speculative gain in tool
selection.

The honest read is that the current shape is fine at eleven tools and won't be
at twenty. The boundary to watch for is a *task* — something with a beginning and
an end, like filling out a form or booking a flight — because that has the phase
boundary a handoff wants. Route on that when it appears, not on tool count.

One thing already confirmed: handoffs that swap instructions do not work on
`gemini-3.1-flash-live-preview`. They work on the current native-audio model.

**Effort:** medium.

### 10. Long-running background tasks

"Keep an eye on that deploy and tell me when it's done." An asyncio task plus
#3's `generate_reply` to report back when it finishes. Very FRIDAY, and mostly
free once #3 exists.

**Effort:** medium, mostly reuse.

### 11. A visible presence

The LiveKit React starter (`agent-starter-react`) gives a HUD — live transcript,
speaking state, waveform. Turns her from a process into something on screen.
The template exists; this is wiring, not building.

**Effort:** low.

### 12. Reach her from anywhere

LiveKit supports SIP telephony, so she can take a phone call. Call your own
house and ask IRA to start the render. **Verify first** — the SIP setup path,
which I haven't touched in this project.

**Effort:** medium.

### 13. Know it's you

Speaker verification, so she only takes orders from the boss's voice
(Resemblyzer or SpeechBrain's ECAPA embeddings; enroll once, compare per
utterance).

This is the highest-effort item here, and the one that makes everything else
safe to build. See below.

**Effort:** high.

---

## Anti-goals

Things that look like progress and aren't:

- **Don't add an STT or TTS stage.** Any idea that starts "just transcribe it
  first" breaks the defining property of this project — the model hears tone
  and replies in tone, and a text bottleneck throws that away. Tests guard
  against it. If you need the text of what was said, the realtime model already
  emits transcripts; use those.
- **Don't grow one monolithic agent.** See #9 — and note that this is the one
  anti-goal the project is currently bending, knowingly, with reasons recorded
  there.
- **Don't put memory in the system prompt.** See #1. The seed/recall split is
  how this stays true as the store grows — 12 facts in the prompt, everything
  else behind a tool call.
- **Don't let a page into the prompt wholesale.** The browsing equivalent of the
  same mistake: page text is capped at 4000 characters and element listings at
  40, because anything she reads lands in context on every later turn.
- **Don't switch to `gemini-3.1-flash-live-preview` for being newer.** It
  ignores `generate_reply()`, which costs you the greeting, all proactivity,
  background reporting, and the entire eval suite in one move. The README's
  model table has the details.
- **Don't make her chatty.** The persona's restraint is the point. A proactive
  assistant with a low bar for speaking is a smart speaker.

---

## Security — read this before adding powerful tools

A voice agent with desktop control has **no authentication on its input
channel**. Anything that makes noise near the microphone can issue commands: a
person in the room, a YouTube video, a podcast, a phone on speaker. There is no
password on a sound wave.

Today the worst case is that someone makes Notepad open. That stays true only
while her tools stay harmless. Add shell execution, file deletion, email
sending, or purchases and the worst case scales with it — and the attack needs
no access to your machine, only to your room.

**Browsing widens this, and it's worth being precise about how.** Chrome runs
with a fresh profile and no logged-in sessions, so a stray voice command can't
read your mail or spend your money. But it can now navigate anywhere and type
anything into a page, which means the worst case moved from "opens Notepad" to
"submits a form on a website". Two things follow:

1. **Don't log her browser into anything you care about.** A persistent profile
   with live cookies converts every one of the above risks into an authenticated
   one. If you ever add a profile path, add the confirmation gate first.
2. **Page content is now untrusted input reaching the model.** Text she reads
   aloud came from a website, and a page can contain instructions aimed at her.
   Today the blast radius is small because her tools are narrow; it grows with
   every tool added. Treat `read_page` output as data, never as direction — the
   same rule that applies to any agent reading the web.

Mitigations, in rough order of value:

1. **Voice verification** (#13) — only the boss's voice counts. This is the
   real fix; everything else is a patch.
2. **A confirmation gate on destructive tools** — she asks before doing, and
   the confirmation goes through the same verification.
3. **Risk-tiered tools.** Opening an app, reading the clipboard: automatic.
   Deleting, sending, spending, executing: never automatic, ever.
4. **Push-to-talk** for sessions where the mic is in a room with other people.
5. **Log every tool call** with what was heard when it fired, so you can audit
   what she did and why.

Related open item: the Gemini API key was pasted into a chat window during
setup and should be rotated — the setup works, so cycling it costs nothing.
There's also a hardcoded key in `gemini_API_checker.py` in the parent directory
that belongs in an environment variable.

---

## If you want a suggested order

| # | Build | Why here |
|---|---|---|
| ~~1~~ | ~~Memory (#1)~~ | ✅ Built — biggest felt change, and nothing else depended on it |
| 1 | System awareness (#6) | An afternoon's work, and it feeds proactivity |
| 2 | Proactivity, agent-side (#3) | Where she stops being a chatbot |
| 3 | Desktop control, the easy verbs (#5) | Daily-use utility, low risk |
| 4 | Split into multiple agents (#9) | Overdue — eleven tools on one prompt now |
| 5 | Wake word (#2) + voice verification (#13) | Together, never separately |
| 6 | Vision (#4) | The big one, once the foundation holds |

Tell me which of these you want and I'll build it — tests first, as the rest of
this project is built.
