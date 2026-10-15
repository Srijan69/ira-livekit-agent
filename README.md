<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# LiveKit Agents Starter - Python

A complete starter project for building voice AI apps with [LiveKit Agents for Python](https://github.com/livekit/agents) and [LiveKit Cloud](https://cloud.livekit.io/).

This project includes:

- **IRA**, a voice assistant who addresses you as "boss", leads with the answer instead of a preamble, volunteers an opinion when she thinks you're about to make a mistake, and opens the conversation with a greeting rather than waiting to be spoken to. Her name doesn't stand for anything, and she'll tell you so.
- **A single Google Gemini Live realtime model** (`gemini-2.5-flash-native-audio-latest`) doing speech-to-speech directly — there is deliberately **no separate STT or TTS stage**
  - Configured in [`src/agent.py`](src/agent.py) via `google.realtime.RealtimeModel`, pinned with the `GEMINI_LIVE_MODEL` constant
  - Voice is pinned to `Kore` with the `GEMINI_VOICE` constant. Google publishes a style descriptor per voice but never a gender, and native-audio models choose their own delivery, so the feminine read is also asked for in the instructions
  - Turn-taking is handled by Gemini's own voice activity detection, so no LiveKit turn detector is used
  - Requires `GOOGLE_API_KEY` in `.env.local`
  - Other [realtime models](https://docs.livekit.io/agents/models/realtime) are available through the plugin ecosystem
- **An `open_app` tool** that launches desktop apps on voice command ("open Spotify") by typing into the Windows Start menu with PyAutoGUI — see [Desktop control](#desktop-control) for its limits
- **Codex-style web browsing** over Selenium — IRA drives a real Chrome window you can watch, enumerates what's on the page as a numbered list, and acts by number ("click three"). See [Web browsing](#web-browsing)
- **Durable memory** in a local SQLite database with full-text search, so facts about you survive a restart. `remember`, `recall`, and `forget` are voice-callable. See [Memory](#memory)
- **Latency tuning** for the realtime loop: configured end-of-turn detection, context window compression, and a process prewarm — see [Latency](#latency)
- Eval suite based on the LiveKit Agents [testing & evaluation framework](https://docs.livekit.io/agents/start/testing/)
- [Background voice cancellation](https://docs.livekit.io/transport/media/noise-cancellation/)
- Deep session insights from LiveKit [Agent Observability](https://docs.livekit.io/deploy/observability/)
- A Dockerfile ready for [production deployment to LiveKit Cloud](https://docs.livekit.io/deploy/agents/)

Ideas for where IRA goes next — a wake word, proactivity, screen vision — are in [ROADMAP.md](ROADMAP.md), along with the anti-goals and the security notes that matter before giving a voice agent more power over your desktop.

### Choosing the Gemini Live model

Only models advertising `bidiGenerateContent` can be used for realtime. The
plugin's own default (`gemini-2.5-flash`) does **not** support it, so
`GEMINI_LIVE_MODEL` must always name a live model explicitly.

| Model | Trade-off |
|---|---|
| `gemini-2.5-flash-native-audio-latest` | **Current setting.** Native speech-to-speech; `generate_reply()` works, so IRA greets first and the in-process eval framework can drive turns. Also supports affective dialog and proactivity. |
| `gemini-2.5-flash-native-audio-preview-12-2025` | Same family, version-pinned so behavior can't drift. |
| `gemini-3.1-flash-live-preview` | Newest, but `generate_reply()`, `update_instructions()`, and `update_chat_ctx()` are ignored — so the agent never speaks the opening greeting and `session.run(...)` evals do not work. Multi-agent handoffs that swap instructions don't either. |

To switch, change `GEMINI_LIVE_MODEL` in [`src/agent.py`](src/agent.py). Two
tests pin the current choice: one asserts the model supports
`bidiGenerateContent` at all, the other that it accepts `generate_reply()`. So
moving to `gemini-3.1-flash-live-preview` fails loudly rather than silently
shipping an agent that never greets.

This starter app is compatible with any [custom web/mobile frontend](https://docs.livekit.io/frontends/) or [telephony](https://docs.livekit.io/telephony/).

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
"open the workflows folder"; IRA opens the local folder in Explorer instead of+trying to navigate to it as a website.

Every Selenium call blocks, so each one is dispatched through
`asyncio.to_thread`. A synchronous `driver.get()` on the event loop would stall
the audio stream carrying IRA's own voice, and she'd audibly stutter
mid-sentence.

## Memory

Facts about you live in a local SQLite database, so they survive a restart. The
tools are `remember`, `recall`, and `forget`, and the store is
[`src/memory.py`](src/memory.py).

The design constraint that shapes it: **don't load everything every session.** A
voice agent pays for context twice — once in tokens on every turn, and once in
latency, because a bigger prompt is a slower first token. So there are two
deliberately separate paths:

- `seed()` runs once at session start and puts **at most 12** facts in front of
  the model, ranked by how often and how recently they've actually proved
  useful. Preferences outrank plain facts, since they change how she should
  answer rather than what she knows.
- `recall()` runs on demand and searches everything.

That split is the difference between memory that scales and a system prompt that
grows until every reply is slow.

Other details:

- **Search is FTS5** with BM25 ranking and a porter/unicode61 tokenizer, so a
  question phrased differently still finds the fact. Search terms are OR-ed
  rather than AND-ed for the same reason. Some Python builds ship an SQLite
  without FTS5, so there's a `LIKE` fallback — a memory that silently stops
  finding things is worse than a slower one.
- **Spoken queries are quoted before they reach FTS5.** Bare `AND`, `OR` and
  `NEAR` are FTS5 syntax, so "coffee or tea" would otherwise raise instead of
  searching.
- **Re-saying a known fact doesn't create a second row.** Dedupe is
  exact-match only: deciding that two differently-worded facts are the same is a
  judgement call, and getting it wrong silently drops something you asked to
  keep.
- **Facts are capped at 500 characters** and categorized into a fixed set
  (`fact`, `preference`, `person`, `project`, `event`). Free-form categories
  would fragment into "preference", "preferences" and "pref" within a week, and
  none of them would match.
- **IRA is instructed never to invent a memory.** When nothing is on file,
  `recall` returns "Nothing on file about that, boss." rather than an empty
  string — an empty result would leave the model free to fill the silence.
- **The database is gitignored.** It holds personal facts about whoever runs the
  agent.

The file defaults to `ira_memory.db` in the project root. Override it with
`IRA_MEMORY_DB`:

```console
IRA_MEMORY_DB=D:\ira\memory.db uv run python src/agent.py console
```

> **On LiveKit Cloud, set `IRA_MEMORY_DB` to a path on a mounted volume.**
> Container filesystems there are ephemeral, so the default path means memory
> quietly resets on every deploy — which looks like a bug in recall rather than
> a storage problem.

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

Screen and mouse tools (`capture_screen`, `move_mouse`, `click_mouse`, and
`type_on_desktop`) run only on the computer hosting the agent. Screen captures
are saved as temporary PNG files for the current run; desktop coordinates are
validated against the active display before input is sent.

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

The last four tests open a real Gemini Live session to check that IRA greets in character, leads with the answer instead of filler, doesn't invent a meaning for her name, and that "open Notepad" actually reaches `open_app`. They skip automatically when `GOOGLE_API_KEY` is unset.

Three of those four are graded by a *judge* — a separate plain-text LLM that reads the reply and answers a yes/no question about it. It's pinned to `gemini-3.5-flash` in the `JUDGE_MODEL` constant and can be overridden with the `IRA_JUDGE_MODEL` environment variable. That override exists because the Gemini free tier meters requests **per model per day** and each judged eval spends one, so a few full-suite runs exhaust it. When that happens those three tests **skip** rather than fail — a billing ceiling isn't evidence about whether IRA is in character, and a suite that goes red for a non-code reason is one you stop trusting. To keep going, point the judge somewhere else:

```console
IRA_JUDGE_MODEL=gemini-3-flash-preview uv run pytest
```

It's pinned to a concrete model rather than an alias like `gemini-flash-latest` on purpose: an alias that drifts to a new model silently changes the grading standard, and an eval whose bar moves on its own isn't a test. If you swap it, confirm the replacement actually answers first — the models-listing API still advertises retired models (`gemini-2.5-flash-lite` is listed as supporting `generateContent`, and 404s on use).

Two details in the live evals are deliberate and worth keeping. The greeting test does *not* judge "leads with the answer" — a greeting has no substance to lead with, so "Hello, boss. What can I do for you?" is the right reply and reads as filler to a judge told to look for filler; that trait is checked separately on a question that has an actual answer. And the `open_app` test compares the app name case-insensitively rather than passing `arguments=` to `contains_function_call`, which compares with `!=`: nothing promises the model will echo your capitalization, so an exact match fails on "notepad" — a spelling difference, not a behavior change.

Note that response-level evals (`session.run(...)`) only work on the `native-audio` models: the framework drives every turn through `generate_reply()`, which `gemini-3.1-flash-live-preview` rejects.

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
