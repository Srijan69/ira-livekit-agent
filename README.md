<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# LiveKit Agents Starter - Python

A complete starter project for building voice AI apps with [LiveKit Agents for Python](https://github.com/livekit/agents) and [LiveKit Cloud](https://cloud.livekit.io/).

This project includes:

- **IRA**, a voice assistant who addresses you as "boss", leads with the answer instead of a preamble, volunteers an opinion when she thinks you're about to make a mistake, and opens the conversation with a greeting rather than waiting to be spoken to. Her name doesn't stand for anything, and she'll tell you so.
- **Google Gemini Live with automatic failover**: `gemini-3.8-live` first, then `gemini-2.5-flash-native-audio-latest`. Both do speech-to-speech directly, with **no separate STT or TTS stage**
  - Configured in [`src/realtime_model.py`](src/realtime_model.py) and used by [`src/agent.py`](src/agent.py)
  - Voice is pinned to `Kore` with the `GEMINI_VOICE` constant. Google publishes a style descriptor per voice but never a gender, and native-audio models choose their own delivery, so the feminine read is also asked for in the instructions
  - Turn-taking is handled by Gemini's own voice activity detection, so no LiveKit turn detector is used
  - Requires `GOOGLE_API_KEY` in `.env.local`
  - Other [realtime models](https://docs.livekit.io/agents/models/realtime) are available through the plugin ecosystem
- **An `open_app` tool** that launches desktop apps on voice command ("open Spotify") by typing into the Windows Start menu with PyAutoGUI — see [Desktop control](#desktop-control) for its limits
- **Codex-style web browsing** over Selenium — IRA drives a real Chrome window you can watch, enumerates what's on the page as a numbered list, and acts by number ("click three"). See [Web browsing](#web-browsing)
- **Durable memory** in a local SQLite database with full-text search, so facts about you survive a restart. `remember`, `recall`, and `forget` are voice-callable. See [Memory](#memory)
- **A Windows desktop dashboard** with voice and typed commands, saved tasks, current weather with a city selector, and system readings. See [Desktop dashboard](#desktop-dashboard)
- **Latency tuning** for the realtime loop: configured end-of-turn detection, context window compression, and a process prewarm — see [Latency](#latency)
- Eval suite based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/)
- [Background voice cancellation](https://docs.livekit.io/transport/media/noise-cancellation/)
- Deep session insights from LiveKit [Agent Observability](https://docs.livekit.io/deploy/observability/)
- A Dockerfile ready for [production deployment to LiveKit Cloud](https://docs.livekit.io/deploy/agents/)

Ideas for where IRA goes next — a wake word, proactivity, screen vision — are in [ROADMAP.md](ROADMAP.md), along with the anti-goals and the security notes that matter before giving a voice agent more power over your desktop.

### Choosing the Gemini Live model

IRA uses the **Gemini Developer API with your Google AI Studio key**, with
`vertexai=False` explicitly set. These are Live API models that accept audio
and return audio; an ordinary text-generation Gemini model cannot replace them.

| Model | Role in IRA |
|---|---|
| `gemini-3.8-live` | **Primary.** Low-latency native audio and tool use. Standard Live mode; no configurable thinking setting. |
| `gemini-2.5-flash-native-audio-latest` | **Fallback.** Another native-audio family using the same key and Kore voice. This older alias may change or retire. |
| `gemini-3.1-flash-live-preview` | Optional override. Current LiveKit plugins support greetings, context/instruction updates and handoffs. Google schedules its retirement for November 17, 2026. |
| `gemini-2.5-flash-native-audio-preview-12-2025` | Optional dated fallback override. Google schedules its retirement for November 17, 2026. |

Optional `.env.local` settings are `IRA_GEMINI_PRIMARY_MODEL` and
`IRA_GEMINI_FALLBACK_MODEL`; blank values use the defaults above. Duplicate
models and incompatible choices are rejected. Extended Thinking is excluded
because its interaction-status and nonblocking-tool lifecycle needs additional
integration. No new key is needed for failover, although model access and quota
still depend on your Google project.

LiveKit preserves the conversation, instructions and tools when selecting the
fallback. **An interrupted user request is not automatically replayed**, because
a tool may already have acted. IRA reports the swap in status/logs; repeat the
request if needed. Only the opening greeting gets one retry after a swap, with
tools disabled. If both providers fail, the session reports the failure.

The lockfile selects LiveKit Agents / Google plugin 1.8.8. A small compatibility
shim forwards generation-future failures to the SDK fallback adapter; rerun its
offline tests when upgrading the SDK. The previous claim that Gemini 3.1 cannot
greet or update instructions applied to older plugin versions (before 1.8.2).

Model support was checked against the [LiveKit Gemini plugin docs](https://docs.livekit.io/agents/models/realtime/plugins/gemini/),
[Google's Gemini 3.8 Live page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
and [retirement schedule](https://ai.google.dev/gemini-api/docs/deprecations) on
October 15, 2026. This implementation was verified with fake providers; live
access, voice quality and provider failover have not been revalidated on your key.

This starter app is compatible with any [custom web/mobile frontend](https://docs.livekit.io/frontends/) or [telephony](https://docs.livekit.io/telephony/).

## Desktop dashboard

The Electron app starts the local bridge and IRA's agent process. From the repo
root, install Python dependencies with `uv sync`, then:

```powershell
cd dashboard
npm ci
npm run dev
```

Open **Settings → Connection and Keys**. Enter your **Google API key** (from
Google AI Studio), **LiveKit URL**, **LiveKit API key**, and **LiveKit API
secret** (from the same LiveKit project), then click **Save and validate**.
IRA validates both providers before saving and reconnects its agent and bridge
automatically. Internet access and access to the configured Gemini Live model
are required for voice conversations.

Credentials are encrypted using the operating system key store in the app's
user-data directory. Saved secrets stay hidden; leave a saved key field blank
to retain it when updating other settings. A fresh desktop profile stays
offline until it has its own keys; the desktop app does not use credentials
from the project's `.env.local` or inherited environment. Terminal and cloud
launches still use environment configuration. Launch the desktop app without
`IRA_EXTERNAL_AGENT` so it can manage the worker with your saved credentials.

Use `npm run build` followed by `npm start` for a production build. For a browser
preview with a scripted voice session and no microphone, use `npm run dev:web`
and open <http://localhost:5273>. The browser preview has no desktop bridge, so
tasks show an unavailable message and system rings show dashes; weather uses
Open-Meteo. In Electron, voice and typed commands both go to IRA over LiveKit.

For a Windows desktop shortcut, run `npm run build`, then
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/create-shortcut.ps1`
from `dashboard/`. Double-click **IRA** on the Desktop to open the app and start
its bridge and voice agent without a terminal. Launching again focuses the
existing window. The shortcut uses this checkout and its installed dependencies;
after code changes, run `npm run build` to update it. If you move the checkout,
run the shortcut script again.

- **Tasks:** ask IRA to add, list or complete a task. The Home panel shows eight
  unfinished tasks; **View All** fetches up to 100. Checkbox changes save through
  the authenticated bridge, with immediate feedback and rollback on failure.
  Voice writes notify the panel to reload. Tasks and memory share `ira_memory.db`
  (override with `IRA_MEMORY_DB`), using WAL and a five-second busy timeout.
- **Dates:** IRA calls `current_time` when resolving “tomorrow” and asks when a
  time is ambiguous. Deadlines are stored in UTC and displayed in local time.
  Tasks are a to-do list; **they do not trigger timed alarms or notifications**.
- **Weather:** Hyderabad, India by default. Click the arrow beside the city,
  search, and choose a location. The selection and last good reading are saved
  locally. Weather refreshes every 15 minutes while visible; a failed refresh
  retains the previous reading with its age.
- **System status:** the rings use Electron's metrics; IRA's `system_status`
  tool reads CPU, memory, disk and battery with Python `psutil`. A computer
  without a battery returns an unavailable battery reading.
- **Quick actions:** cards ask one focused question for the missing topic,
  then help through IRA. File attachments remain disabled. The name/tier in
  `dashboard/src/lib/profile.ts` are local display defaults, not account data.

Only Home is implemented. The other sidebar pages show “Not built yet”. Tray,
hotkey, autostart and installer work remain sub-project D in the
[handoff](docs/handoff/HANDOFF.md).

Dashboard checks: `npm test`, `npm run typecheck`, and `npm run build` from
`dashboard/`. The widget tests use fake IPC, weather responses and rooms. The
[C implementation preview](docs/handoff/current-home-widgets.png) uses sample
data and a scripted session, not live provider or personal data.

## Desktop control

The `open_app` tool presses <kbd>Win</kbd>, types the app name into the Start
menu, and presses <kbd>Enter</kbd>. Worth knowing:

- **It only works when the agent runs on your own machine.** PyAutoGUI needs a
  desktop, so on the LiveKit Cloud deployment the tool returns a spoken
  explanation instead of failing the session. `pyautogui` is imported lazily for
  the same reason — a top-level import would take the whole agent down there.
- **It types into whatever window has focus.** Anything that exercises the tool
  for real will act on your desktop, which is why the tests fake the module and
  the simulations mock the tool.
- **It reports "asked Windows to open X", not "opened X."** Pressing Enter at the
  Start menu is not proof the app launched, and IRA shouldn't claim more than
  she knows.
- App names arrive via speech through the model, so they're sanitized before
  being typed: control characters are stripped (a `\n` inside `write()` is
  pressed as Enter, which would submit early and run the rest as its own query)
  and the name is capped at 64 characters.

## Web browsing

IRA drives a real Chrome window through Selenium, in the same shape a coding
agent does: she never sees a DOM, she sees a numbered list of the things on the
page she can act on, and she acts by number.

```
1. link Sign in
2. textbox Search
3. button Submit
```

That shape is the only one that survives a voice channel — a CSS selector isn't
something anyone can say out loud, but "click three" is. The tools are
`browse_to`, `read_page`, `list_page_elements`, `click_element`,
`type_into_element`, `go_back`, and `close_browser`, all in
[`src/browser.py`](src/browser.py).

Worth knowing:

- **The window is visible by default**, because the point is that you can watch
  it work. Set `IRA_BROWSER_HEADLESS=1` for CI or any host without a display.
- **Chrome starts on the first `browse_to` and is then reused.** Starting it
  costs a few seconds; paying that once is the difference between a usable tool
  and one you stop asking for.
- **No chromedriver to install.** Selenium 4.6+ ships Selenium Manager, which
  resolves and downloads a matching driver on its own.
- **The numbering is re-derived on every listing and thrown away after every
  click or navigation.** Stale indices are the main failure mode of this style
  of tool — saying "click three" and getting whatever is third on a page that
  already moved. IRA is instructed to list again after anything changes.
- **Like `open_app`, this only works when the agent runs on your own machine.**
  There's no display on LiveKit Cloud, so `BrowserUnavailableError` is caught
  and turned into a spoken explanation instead of a dropped turn. Selenium is
  imported lazily for the same reason.
- **Everything is capped**, because page content is both spoken aloud and
  carried in context on every later turn: 40 elements per listing, 4000
  characters of page text, 80 characters per element label. IRA is told to
  summarize a page rather than recite it.
- **Page loads time out at 20 seconds**, well below Selenium's 30-second
  default. On a voice call a long load is just silence, with no way for her to
  explain herself.

### Signed-in Chrome or Brave and Spotify

Set `IRA_BROWSER=chrome` (the default) or `IRA_BROWSER=brave` before starting
the agent. The browser reuses the Windows profile named by
`%LOCALAPPDATA%\\IRA\\Browser`, so it can run alongside your normal browser
without a profile-lock error. Sign in to Spotify once in IRA's browser window;
the cookies remain available on later runs. To use a specific profile directory,
set `IRA_BROWSER_PROFILE` to its full path.

```powershell
$env:IRA_BROWSER = "chrome"
$env:IRA_BROWSER_PROFILE_NAME = "Default"
uv run python src/agent.py console
```

Ask IRA to open Spotify in Chrome or Brave, then say "play", "pause", "rewind ten seconds",
or "forward thirty seconds". These controls operate on the Spotify Web Player
tab in IRA's persistent profile. To see the saved workflow files, say
"open the workflows folder"; IRA opens the local folder in Explorer instead of
+trying to navigate to it as a website.

Every Selenium call blocks, so each one is dispatched through
`asyncio.to_thread`. A synchronous `driver.get()` on the event loop would stall
the audio stream carrying IRA's own voice, and she'd audibly stutter
mid-sentence.

## Memory

Open **Settings → Your Profile** to save your name, optional age, occupation,
and preferences. These fields are available before the next greeting, and edits
reach a running IRA session at its next quiet moment. Blank fields stay absent.
Explicit voice corrections such as “my name is Alex” update this same profile.

**Settings → Memory and Context** controls automatic summaries and cross-session
recall. Inspect and search remembered facts and conversation records, delete
individual records, or clear conversation memory while keeping your profile.
Profile reset is separate. The default application context budget is **16,000
tokens**, with summary preparation around **12,000**; these are application
settings, not a claim about the model's full capacity. Advanced settings expose
the budget, trigger, recent-turn count and text summarizer model.

IRA journals finalized human turns, committed replies and tool outcomes in
SQLite. A background, tools-disabled Gemini request prepares a structured rolling
summary using your configured **Google API key**. The default summarizer is
`gemini-3.8-flash`; provider availability and quota failures are reported without
discarding the journal. Once idle, IRA updates its instructions/history and
starts a fresh voice connection. “Context restored” means the local context was
updated; “Ready” requires activity from the current provider connection.

Recalled tasks and tool records are background data and never replay old actions
or approvals. The recent context is bounded; `recall` can search archived details
omitted by a summary. Deletions and explicit corrections invalidate affected
summaries and prevent late workers from restoring old source material. Existing
curated `USER.md` and `MEMORY.md` remain readable, with the structured profile
taking precedence.

The default database is `memories/personal.sqlite3`. Set
`IRA_PERSONAL_MEMORY_DB` to choose another file, or `IRA_MEMORIES_DIR` to move
the memory directory. The bridge and agent must use the same path. For cloud
deployments, choose persistent mounted storage to retain memory across deploys.
Implementation: [`src/personal_memory.py`](src/personal_memory.py),
[`src/context_memory.py`](src/context_memory.py), and
[`src/session_memory.py`](src/session_memory.py).

## Workflows and durable notes

Repeatable procedures are stored as editable Markdown files in `workflows/`.
IRA can save, list, retrieve, and delete them with voice commands. Say things
like "remember this workflow" or "always do it this way" after completing a
multi-step task. Use `IRA_WORKFLOWS_DIR` to move the workflow library to a
backed-up location.

Important facts, preferences, unfinished tasks, and outcomes can be saved as
durable notes. IRA loads a small recent-note set at startup and searches the
full SQLite memory store when you ask about older conversations.

Stable personal preferences have their own memory category. Say "remember that
I prefer concise answers", "remember I dislike phone calls", or "save this as a
preference". IRA stores the preference permanently in SQLite, reloads the most
useful preferences at the next session, and can answer "what do you know about
my preferences?" using the `list_preferences` tool. Remove one with the normal
"forget" command.

### Workflow execution and terminal status

Detailed workflows use explicit steps such as `spotify_control: action=play`,
an `allowed_tools` list, prerequisites, verification rules, and recovery notes.
IRA executes only those declared actions and stops at the first unverified
failure. Workflows with at most five linear deterministic steps use the
lightweight worker. Longer workflows, branching or retry policies, screen
requirements, and unknown actions use the powerful worker boundary.

The terminal prints a correlated run id and states including `LISTENING`,
`RECOGNIZING`, `GENERATING`, `DELEGATING`, `RUNNING`, `VERIFYING`, `DONE`, and
`ERROR`. These lines are diagnostics and are not added to the model prompt.

Screen and mouse tools run only on the computer hosting the agent. `capture_screen`
returns an image, its pixel dimensions, an observation ID and available Windows
accessibility targets. Use `click_desktop_element` for a named control; pixel
tools use the displayed image's coordinates and latest observation ID. The
controller maps them to native pixels and checks foreground/window ownership.
Screen captures are saved as temporary PNG files. See [computer-use targeting](#computer-use-targeting-correction-18-october-2026).

## Latency

Three things in [`src/agent.py`](src/agent.py) exist purely to cut dead air. All
three are latency settings, not behavior settings:

| Setting | What it does |
|---|---|
| `_END_OF_TURN_SILENCE_MS = 450` with high end-sensitivity | Tells Gemini's server-side VAD how long a pause means "your turn". Unconfigured, it waits conservatively, which is most of the delay you feel between finishing a sentence and hearing a reply. |
| `_CONTEXT_TRIGGER_TOKENS = 16000` / `_CONTEXT_TARGET_TOKENS = 12000` | Sliding-window context compression. Without it a long session grows its own prompt until every turn is slow, and eventually hits the window ceiling mid-conversation. |
| `server.setup_fnc = _prewarm` | Imports the heavy dependency graph once per worker process, before a job is accepted, instead of on the first turn of the first call. |

**The prewarm does nothing in `console` mode**, which runs the job in-process
with no warmed worker — the console log says `no warmed process available`. It
pays off under `dev` and `start`. If you're timing the agent from the console,
the VAD setting is the one doing the work.

## Using coding agents

This project is designed to work with coding agents like [Claude Code](https://claude.com/product/claude-code), [Cursor](https://www.cursor.com/), and [Codex](https://openai.com/codex/).

For your convenience, LiveKit offers both a CLI and an [MCP server](https://docs.livekit.io/reference/developer-tools/docs-mcp/) that can be used to browse and search its documentation. The [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) (`lk docs`) works with any coding agent that can run shell commands. Install it for your platform:

**macOS:**

```console
brew install livekit-cli
```

**Linux:**

```console
curl -sSL https://get.livekit.io/cli | bash
```

**Windows:**

```console
winget install LiveKit.LiveKitCLI
```

The `lk docs` subcommand requires version 2.15.0 or higher. Check your version with `lk --version` and update if needed. Once installed, your coding agent can search and browse LiveKit documentation directly from the terminal:

```console
lk docs search "voice agents"
lk docs get-page /agents/start/voice-ai-quickstart
```

See the [Using coding agents](https://docs.livekit.io/intro/coding-agents/) guide for more details, including MCP server setup.

The project includes a complete [AGENTS.md](AGENTS.md) file for these assistants. You can modify this file to suit your needs. To learn more about this file, see [https://agents.md](https://agents.md).

## Dev Setup

Create a project from this template with the LiveKit CLI (recommended):

```bash
lk cloud auth
lk agent init my-agent --template agent-starter-python
```

The CLI clones the template and configures your environment. Then follow the rest of this guide from [Run the agent](#run-the-agent).

<details>
<summary>Alternative: Manual setup without the CLI</summary>

Clone the repository and install dependencies to a virtual environment:

```console
cd agent-starter-python
uv sync
```

Sign up for [LiveKit Cloud](https://cloud.livekit.io/) then set up the environment by copying `.env.example` to `.env.local` and filling in the required keys:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`

You can load the LiveKit environment automatically using the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/):

```bash
lk cloud auth
lk app env --write --destination .env.local
```

</details>

## Run the agent

Run this command to speak to your agent directly in your terminal:

```console
.venv\Scripts\Activate.ps1
uv run python src/agent.py console
```

IRA greets you as soon as the session connects, so you don't have to speak
first. That depends on the model accepting `generate_reply()` — see the model
table above.

> **On Windows:** run this in a normal terminal window. If you redirect or pipe
> the output (`> log.txt`, `| findstr`, ...), Python falls back to the `cp1252`
> code page and the SDK crashes with `UnicodeEncodeError` printing its own
> startup emoji. Set `PYTHONUTF8=1` if you need to capture output.

To run the agent for use with a frontend or telephony, use the `dev` command:

```console
uv run python src/agent.py dev
```

> This agent is registered with `agent_name="my-agent"` in
> [`src/agent.py`](src/agent.py), which puts it on **explicit dispatch**: it
> will not automatically join rooms your frontend creates. Either set up a
> [dispatch rule](https://docs.livekit.io/agents/worker/agent-dispatch/), or
> remove the `agent_name` argument to fall back to automatic dispatch.

In production, use the `start` command:

```console
uv run python src/agent.py start
```

## Frontend & Telephony

Get started quickly with our pre-built frontend starter apps, or add telephony support:

| Platform | Link | Description |
|----------|----------|-------------|
| **Web** | [`livekit-examples/agent-starter-react`](https://github.com/livekit-examples/agent-starter-react) | Web voice AI assistant with React & Next.js |
| **iOS/macOS** | [`livekit-examples/agent-starter-swift`](https://github.com/livekit-examples/agent-starter-swift) | Native iOS, macOS, and visionOS voice AI assistant |
| **Flutter** | [`livekit-examples/agent-starter-flutter`](https://github.com/livekit-examples/agent-starter-flutter) | Cross-platform voice AI assistant app |
| **React Native** | [`livekit-examples/voice-assistant-react-native`](https://github.com/livekit-examples/voice-assistant-react-native) | Native mobile app with React Native & Expo |
| **Android** | [`livekit-examples/agent-starter-android`](https://github.com/livekit-examples/agent-starter-android) | Native Android app with Kotlin & Jetpack Compose |
| **Web Embed** | [`livekit-examples/agent-starter-embed`](https://github.com/livekit-examples/agent-starter-embed) | Voice AI widget for any website |
| **Telephony** | [Documentation](https://docs.livekit.io/telephony/) | Add inbound or outbound calling to your agent |

For advanced customization, see the [complete frontend guide](https://docs.livekit.io/frontends/).

## Tests and evals

Simulations run full multi-turn conversations between a simulated user and your agent on LiveKit Cloud, then judge each transcript. The scenarios live in [`scenarios.yaml`](scenarios.yaml). Run them locally with the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/):

```console
lk agent simulate --scenarios scenarios.yaml
```

The `Simulations` workflow in `.github/workflows/simulations.yml` runs the same file on every merge to `main` and on demand from the Actions tab. It runs there rather than on every pull request push because each run spends real inference. See the [simulations guide](https://docs.livekit.io/agents/start/testing/simulations/) for how to write scenarios and read results.

Simulations run on LiveKit Cloud, which has no desktop, so `open_app` is mocked during them. There's no YAML key for that — the mock is wired in the entrypoint in [`src/agent.py`](src/agent.py), guarded by `ctx.simulation_context()` so it's a no-op in production. A scenario can choose what the mock returns via `userdata.open_app_result`, which is how the failure path gets exercised.

For turn-level checks that don't need a live session, the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/) runs your agent in-process under `pytest`. Run them with `uv run pytest`.

[`tests/test_agent.py`](tests/test_agent.py) guards the realtime-only constraint — no separate STT or TTS stage, and a `GEMINI_LIVE_MODEL` that actually supports `bidiGenerateContent` and `generate_reply()` — plus the IRA persona, the pinned voice, and the `open_app` tool. The PyAutoGUI key sequence is asserted against a fake module, so the tests never touch your desktop.

[`tests/test_browser_and_memory.py`](tests/test_browser_and_memory.py) covers the two new modules directly: element description and numbering, the click-invalidates-numbering rule, the JavaScript click fallback for intercepted clicks, `https` inference for spoken addresses, and every memory path including dedupe, the FTS5-syntax-in-speech case, seed ranking, and durability across a reopen.

Neither file starts a real Chrome or touches the real memory database, and that's a hard rule rather than a convenience. These tools act on your actual machine — a suite that opens windows and saves facts is one nobody can run while working. The browser tests drive `BrowserController` through a fake driver injected into `_driver`; the agent-level tool tests pass a fake browser into `Assistant(browser=...)`; the memory tests get a `tmp_path` database.

Two of the persona tests are identity guards rather than feature tests: one asserts the system prompt never names a borrowed character (IRA is meant to *behave* like Iron Man's assistant, not *be* her — naming the character invites the model to play its own idea of her), and one asserts the prompt states the name isn't an acronym, since otherwise "what does IRA stand for?" gets a confidently invented expansion.

The last four tests open a real Gemini Live session to check that IRA greets in character, leads with the answer instead of filler, doesn't invent a meaning for her name, and that "open Notepad" actually reaches a mocked `open_app`. They require both `GOOGLE_API_KEY` and explicit opt-in with `IRA_RUN_LIVE_EVALS=1`. Normal `uv run pytest -q -p no:randomly` runs stay offline even when `.env.local` contains credentials.

```powershell
$env:IRA_RUN_LIVE_EVALS = "1"
uv run pytest tests/test_agent.py -q -p no:randomly
Remove-Item Env:IRA_RUN_LIVE_EVALS
```

Final verification: 636 backend tests passed (including the real browser
fixture), 231 dashboard tests passed, and typecheck/build/Ruff passed. Separate
live Gemini tool-selection and owned Windows input tests each passed. The
Windows fixture now has a **Start test** button: click it, then leave the mouse
and keyboard idle until it closes. Its independent native events verified
double-click, right/middle clicks, both scroll axes, movement, drag/release and
text entry. This supersedes the earlier foreground-blocked result above.
Microphone/audio interruption and signed-in Spotify remain untested here.

The offline suite also covers concurrent SQLite writers, task CRUD and bridge
authentication, voice tools, reliable task events, metrics with fake psutil,
and provider failover without replaying actions. The task simulation has its
own isolated state; it never writes to the personal task database.

Three of those four are graded by a *judge* — a separate plain-text LLM that reads the reply and answers a yes/no question about it. It's pinned to `gemini-3.5-flash` in the `JUDGE_MODEL` constant and can be overridden with the `IRA_JUDGE_MODEL` environment variable. That override exists because the Gemini free tier meters requests **per model per day** and each judged eval spends one, so a few full-suite runs exhaust it. When that happens those three tests **skip** rather than fail — a billing ceiling isn't evidence about whether IRA is in character, and a suite that goes red for a non-code reason is one you stop trusting. To keep going, point the judge somewhere else:

```console
IRA_RUN_LIVE_EVALS=1 IRA_JUDGE_MODEL=gemini-3-flash-preview uv run pytest
```

It's pinned to a concrete model rather than an alias like `gemini-flash-latest` on purpose: an alias that drifts to a new model silently changes the grading standard, and an eval whose bar moves on its own isn't a test. If you swap it, confirm the replacement actually answers first — the models-listing API still advertises retired models (`gemini-2.5-flash-lite` is listed as supporting `generateContent`, and 404s on use).

Two details in the live evals are deliberate and worth keeping. The greeting test does *not* judge "leads with the answer" — a greeting has no substance to lead with, so "Hello, boss. What can I do for you?" is the right reply and reads as filler to a judge told to look for filler; that trait is checked separately on a question that has an actual answer. And the `open_app` test compares the app name case-insensitively rather than passing `arguments=` to `contains_function_call`, which compares with `!=`: nothing promises the model will echo your capitalization, so an exact match fails on "notepad" — a spelling difference, not a behavior change.

Response-level evals (`session.run(...)`) drive turns through `generate_reply()`.
The supported current plugin implements this for Gemini 3.1 as well as the
configured primary and fallback. Offline checks cannot establish provider
availability or assess spoken response quality; use the opt-in evals for that.

## Using this template repo for your own project

Once you've started your own project based on this repo, you should:

1. **Check in your `uv.lock`**: This file is currently untracked for the template, but you should commit it to your repository for reproducible builds and proper configuration management. (The same applies to `livekit.toml`, if you run your agents in LiveKit Cloud)

2. **Add your own repository secrets**: You must [add secrets](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/using-secrets-in-github-actions) for `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` so that the simulations can run in CI.

## Deploying to production

This project is production-ready and includes a working `Dockerfile`. To deploy it to LiveKit Cloud or another environment, see the [deploying to production](https://docs.livekit.io/deploy/agents/) guide.

## Self-hosted LiveKit

You can also self-host LiveKit instead of using LiveKit Cloud. See the [self-hosting](https://docs.livekit.io/transport/self-hosting/local/) guide for more information. If you choose to self-host, you'll need to also use [model plugins](https://docs.livekit.io/agents/models/#plugins) instead of LiveKit Inference and will need to remove the [LiveKit Cloud noise cancellation](https://docs.livekit.io/transport/media/noise-cancellation/) plugin.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
# Verified skills and Hermes adoption (17 October 2026)

The active project folder is `IRA-LiveKit_Agent`. The Gemini Live voice runtime
and Electron dashboard remain in place. See
[`docs/superpowers/plans/2026-10-17-hermes-adoption.md`](docs/superpowers/plans/2026-10-17-hermes-adoption.md)
for the implementation record and research references.

The **Automation → Skills** page lists procedures, drafts, versions and run
history. It supports pinning, recoverable archive/restore, version comparisons,
and Run/Stop through the connected voice agent. A draft must pass validation and
be explicitly enabled. Startup includes a compact catalog; specialist browser,
desktop, memory and skill-management tools load through `load_capabilities`.

Included procedures are **Open Desktop App**, **Open Web Page**, and **Play
Spotify Playlist**. For example: “Run Open Web Page” checks `example.com` for
“Example Domain”. Specify both the app and expected title for another app, or
the URL and expected text for another web page. Spotify uses IRA's existing
signed-in Chrome/Brave profile and requires observed audio plus matching player
context; a changed Spotify selector stops the procedure instead of claiming
success.

Every strict tool step has an observable check. Invalid later steps are rejected
before execution, run versions are frozen, duplicate deliveries are not
replayed, and uncertain in-flight actions retain desktop ownership until they
settle. A process-shared lock coordinates direct and skill actions across IRA
sessions. Run history survives restart; live owners are not marked interrupted.
The bridge has authenticated read/manage routes, **no HTTP desktop executor**.

Skill originals and versions live in `workflows/.skills.sqlite3`; authoritative
run evidence lives in `workflows/.runs.sqlite3`. These local files are ignored by
Git. Back up both databases together with `workflows/*.md` while IRA is stopped.
Existing legacy workflow files remain readable, but require explicit checks and
review before execution. Hand edits demote a procedure to draft.

Startup memory shares a 4,000-character budget and removes duplicate entries.
`correct_memory` supersedes an explicit memory ID without erasing its history.
`learn_skill` copies a parameterized, verified successful procedure into a new
draft with source-run provenance. It does not train model weights. Curator
recommendations are review-only and honor pins.

Current scope: strict actions cover app launch, web navigation, Spotify and
screen capture. Click/type/mouse tools remain available through capability
loading, but are not yet compiled skill actions. Visual `looks_like`, interactive
`ask`/`say` steps, arbitrary shell commands, autonomous model compilation,
automatic consolidation, and conversation-history compression beyond Gemini's
existing sliding window are not enabled. Local automated checks cover the
contracts; live Gemini audio and signed-in Spotify still need a manual smoke
test on the intended machine/profile.

Validation commands from the renamed project folder:

```powershell
uv run --no-sync python -m pytest -q
cd dashboard
npm test
npm run typecheck
npm run build
```

## Verified computer actions and clarification (18 October 2026)

This extends the 17 October scope above. Compiled skills now support
`browser_click`, `browser_type`, `desktop_click`, `desktop_type`, and
`desktop_press_key`. Browser steps require a unique current CSS target and an
exact expected HTTP(S) URL, ignoring its fragment. Scheme, host and default
port are normalized; path and query must match. Use the browser's ASCII or
punycode hostname. Clicks never fall back to JavaScript when a target is covered.
Typing reads back the field value and refuses password, hidden, file, disabled,
and read-only fields. Each step still needs an explicit postcondition.

`active_window "Exact title"` checks the foreground window; `page_value`
accepts a JSON string with `selector` and `value` for exact field verification.
The supported CSS subset includes type/ID/class/attribute selectors, ordinary
combinators, simple child/state selectors, and numeric `nth-child`/`nth-of-type`.
Selectors such as `:has()` and `:not()` are rejected during preflight.
Desktop input requires an exact foreground title, rechecks window identity,
and confines clicks to the screen and window, with Windows hit-testing.
Desktop typing currently supports printable ASCII only. A foreground-title
check proves focus, not the contents of a native text field; use a meaningful
observable result before claiming an application task is complete.

An `ask` step pauses the run and releases the desktop lock. IRA asks the
question, then `answer_skill` resumes the same frozen version using its run ID
and question ID. The Skills page shows the question with Answer, Edit answer,
and Stop controls. Invalid answers remain retryable; stale answers cannot fill
a later question. Before continuing, the engine reacquires the lock and checks
the most recent verified state again. Waiting has a 120-second clarification
limit and remains subject to the 120-second total run deadline. A restart
marks an abandoned run uncertain; it never resumes desktop effects automatically.
An explicit `ask` always prompts, even when an initial/default value was given.

Answers remain in the coordinator's memory. Execution arguments, errors, and
evidence after an answer are conservatively redacted from the run ledger;
the chat itself retains its usual transport/history behavior. Waiting runs do
not count as completed attempts; completion, Stop and timeout update counters
once. `say` and visual `looks_like` steps remain unsupported.

[`workflows/fill-web-field.md`](workflows/fill-web-field.md) is a new draft
example. Review and enable it in Skills, then run it with the exact `url` and
`selector` inputs. It asks for text, fills the field, and verifies the value
without submitting the form.

The [Open Computer Use review](docs/research/2026-10-18-open-computer-use-adoption.md)
records the pinned upstream source and the targeting safeguards adopted here.
Its full MCP runtime is not installed: Windows support is experimental and the
inspected guarded-input implementation is newer than its published release.

Optional smoke tests use isolated stores and an owned fixture:

```powershell
$env:IRA_RUN_BROWSER_SMOKE = "1"
uv run --no-sync python -m pytest tests/test_skill_browser_live.py -q
Remove-Item Env:IRA_RUN_BROWSER_SMOKE

# This focuses a temporary test window and moves the pointer inside it.
$env:IRA_RUN_DESKTOP_SMOKE = "1"
uv run --no-sync python -m pytest tests/test_skill_desktop_live.py -q
Remove-Item Env:IRA_RUN_DESKTOP_SMOKE
```

The real Selenium form smoke passed, including clarification, escaped text,
value checks, click verification, duplicate delivery, and refusal cases. The
Windows fixture was attempted but could not gain foreground focus; it refused
before sending any input. Actual desktop input, live Gemini audio interruption,
and signed-in Spotify remain unverified on this machine for this milestone.

## Tool access and execution timeline (18 October 2026)

This update supersedes the earlier capability-loading behavior: all tool
schemas are registered before Gemini connects. The installed provider cannot
change them during a call without restarting its connection. `load_capabilities`
now lists tools already available; skill bodies and stored context are still
retrieved as needed. A live Gemini test selects `move_mouse` and `scroll_mouse`
through the real tools with a harmless physical desktop backend.

Home now shows an execution timeline beside the orb. It displays actual tool
names, numbered skill steps, elapsed time, verification and terminal outcomes.
A waiting skill remains visible when its initial tool call returns; answering
continues the same skill run. Clear finished removes terminal history only.
The timeline contains fixed labels and metadata, not arguments or driver errors.

Mouse tools now cover movement, left/right/middle click, double/triple click,
vertical/horizontal scroll and drag. `scroll_mouse` accepts signed 1–100 wheel
notches (positive up/right); `drag_mouse` accepts on-screen start/end coordinates,
a button and 0.1–5 seconds. On Windows, IRA sends the correct wheel axis and
120-unit notch amount instead of PyAutoGUI's horizontal-to-vertical alias.
Drag cleanup releases its button even when PyAutoGUI's corner abort fires;
cancellation retains host ownership until that cleanup finishes.

Compiled skills additionally support `desktop_move`, `desktop_scroll` and
`desktop_drag` with exact foreground-window guards. `pointer_position` verifies
the cursor with JSON such as `{"x":120,"y":160}`. Numeric skill inputs can be
bound as `{"x":"{x}","y":"{y}"}`; constant coordinates are checked during
preflight even when another coordinate needs an answer. This verifies pointer
position only; a drag/drop or scroll task still needs an observable application
result. Coordinates are currently constrained to the primary screen.

The adopted Hermes features remain: versioned procedures, strict observations,
clarification/resume, persistent run history, bounded memory, explicit learning
drafts and reviewable curator suggestions. Open Computer Use supplied design
references for targeting safeguards; its experimental Windows MCP runtime is
not installed. Autonomous skill synthesis from arbitrary conversations remains
outside this milestone.

The [implementation record](docs/superpowers/plans/2026-10-18-tool-access-and-execution-display.md)
tracks final checks and remaining live-environment limits. Restart the local
IRA app/agent and reconnect to load the new startup schemas. For the harmless
live-provider selection check:

```powershell
$env:IRA_RUN_LIVE_EVALS = "1"
uv run --no-sync python -m pytest tests/test_tool_access_live.py -q
Remove-Item Env:IRA_RUN_LIVE_EVALS
```

## Computer-use targeting correction (18 October 2026)

The previous voice path described native screenshot dimensions while Gemini's
encoder resized the image to 1024 pixels. A model point such as `(819,230)` was
sent directly to Windows, although the intended native point was `(1536,432)`.
`ObservedDesktop` now owns one explicit image-to-desktop mapping under a Windows
physical-pixel DPI context. The image and observation ID are sent together as
discrete model context. The provider does not resize the coordinate image again.

Windows controls are also enumerated through UI Automation. IRA prefers a named
target's current ID over estimating pixels, including Chrome's native profile
picker. IDs expire on a new capture/action, other host or saved-skill actions,
model replacement and after 60 seconds. A click rechecks the control's runtime
identity, label, role, bounds, foreground and hit ownership immediately before
input. Each action returns new evidence; a failed capture never proves success.
Brief window transitions retry observation only, never replay the click.

The blue IRA arrow/ring is a temporary, click-through native Windows overlay.
It follows the real pointer during input, does not activate a window or replace
system cursor settings, and closes on completion or failure. Open Computer Use's
demo overlay is a separate macOS implementation; its full runtime is still not
installed here.

Restart the local IRA agent and reconnect to load these changed tool schemas.
`uv sync` installs the new Windows-only `uiautomation` dependency. Existing
compiled skills keep their native-coordinate contract. The whole-desktop upgrade
below extends voice pixel input to every monitor. ASCII-only desktop typing is unchanged.
Screenshot-only targeting still depends on the vision model: live tests exposed
guessed locations, so named controls use accessibility IDs and IRA is instructed
to ask when a target cannot be resolved confidently.

The [debugging and verification record](docs/research/2026-10-18-computer-use-targeting-fix.md)
includes the actual Chrome `Kommidi` profile check, live Gemini fixture results,
and independent native click/indicator checks. Reproduce the opt-in checks with:

```powershell
$env:IRA_RUN_LIVE_EVALS = "1"
uv run --no-sync python -m pytest tests/test_computer_use_live.py tests/test_tool_access_live.py -q
Remove-Item Env:IRA_RUN_LIVE_EVALS

$env:IRA_RUN_DESKTOP_SMOKE = "1"
uv run --no-sync python -m pytest tests/test_computer_use_native.py tests/test_desktop_indicator_live.py -q
Remove-Item Env:IRA_RUN_DESKTOP_SMOKE
```

The native fixture opens an owned window. Click **Start test** and leave input
idle until it closes. Its second click must create new fixture evidence rather
than reusing the first click's result.

## Whole-desktop capture and task scope (18 October 2026)

`capture_screen` includes all connected monitors, including displays left of or
above the primary screen. Its overview reports the physical desktop origin,
monitor rectangles, current pointer position and visible window IDs.
`inspect_screen` provides a detailed crop from the original full-resolution
capture with a new observation ID. Each image has its own pixel coordinates;
IRA maps them to physical coordinates once, rejecting monitor gaps and changed
display layouts. `focus_desktop_window` selects an observed window and verifies
the fresh foreground before further input.

Desktop, browser and saved-skill tools share a request controller. Only actual
typed or finalized spoken input starts a request; screenshot messages cannot
authorize actions. A Gemini policy check compares each proposed action with the
request, prior outcomes and screen evidence. IRA can perform the necessary steps,
ask for an ambiguous target, and call `finish_request` after verifying completion.
If the model reports the result without calling that tool, a completion check
closes the request after the final speech and all related tools have settled.
It compares the report with observed results and preserves pending questions.
`stop_actions`, interrupted input and new requests invalidate pending actions.
Saved-skill steps use the same checks, including after a clarification answer.

`IRA_REQUEST_SCOPE_MODEL` defaults to `gemini-3.5-flash-lite`, using the existing
`GOOGLE_API_KEY`. Policy errors refuse input and ask for a retry. This adds a model
check before effects; natural-language and visual judgments remain probabilistic.
Request identity, permit reuse, expiry, cancellation and coordinate transforms
are enforced in code. The blue click-through IRA pointer marker remains active
during input. Captures and model-image history are bounded and owned files are
cleaned when the session ends.

Restart the local agent and reconnect to load the updated tools and lifecycle
hooks. See the [implementation evidence](docs/research/2026-10-18-scoped-whole-desktop-verification.md)
for results and physical-monitor coverage. Run policy-only synthetic checks with:

```powershell
$env:IRA_RUN_SCOPE_LIVE = "1"
uv run --no-sync python -m pytest tests/test_request_scope_live.py -q
Remove-Item Env:IRA_RUN_SCOPE_LIVE
```
