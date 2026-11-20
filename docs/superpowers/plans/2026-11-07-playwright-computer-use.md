# Playwright and Computer Use Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement the independent tasks with tests and review.

**Goal:** Replace Selenium entirely with Playwright and upgrade IRA's guarded desktop fallback and both computer-use visual views.

**Architecture:** A native Playwright owner thread serves synchronous browser methods under existing request guards. Observed desktop actions publish bounded before/after screenshots over LiveKit text streams to the existing dashboard; native Windows input retains focus/freshness checks and shows an unobtrusive target indicator.

**Tech Stack:** Python/Playwright, Windows SendInput/UI Automation, LiveKit 1.8.8, React/TypeScript/Electron, pytest/Vitest.

**Spec:** `docs/superpowers/specs/2026-11-07-playwright-computer-use-design.md`.

## Global constraints

- Preserve all prior uncommitted changes and personal data; no commits or session restart required.
- Chrome and Brave only, using existing IRA persistent profiles. No browser substitution.
- Copied request contextvars, current-request checks, single-use desktop evidence, and uncertain-effect classification remain.
- Screenshot events use `computer.observation` text streams and a 512 KiB bound; no typed text in action metadata.
- `phase`: observed/before/after/unavailable/cleared; monotonically increasing sequence per run.
- Python checks use `uv run python -m pytest` and `uv run python -m ruff`.

### Task 1: Native Playwright browser

**Owner:** playwright_migration. **Files:** src/browser.py, new browser_runtime.py, pyproject.toml/uv.lock, browser tests and evaluation scripts.

**Produces:** synchronous BrowserController navigation/forms/tabs/Spotify APIs plus current_url, is_open, evaluate(expression,arg=None), page_html().

- [x] Add failing owner-thread/contextvar tests and real local HTML tests for reference invalidation, native clicks, controls, tab lifecycle and Spotify identity.
- [x] Replace driver implementation with Playwright context/page/locator operations and bounded waits; remove driver-shaped fakes and all runtime/dependency imports.
- [x] Verify browser fixtures, negative effects and Brave preference; return caller migration inventory.

```python
def test_browser_reference_does_not_select_replacement(browser_fixture):
    browser_fixture.set_content('<button>Save</button>')
    browser_fixture.list_elements()
    browser_fixture.evaluate('() => document.querySelector("button").outerHTML="<button>Delete</button>"')
    with pytest.raises(Exception):
        browser_fixture.click(1)
```

### Task 2: Observed desktop and native indicator

**Owner:** computer_backend. **Files:** src/computer_use.py, desktop*.py and corresponding tests.

**Produces:** act/focus optional `_on_action(frame, descriptor)`; describe captured_at/screenshot_data_url; Unicode typing and focus/control revalidation; real target indicator geometry.

- [x] Demonstrate ignored target points, same-window field/control drift, and rejected Unicode with red tests.
- [x] Implement target metadata and native input/indicator repairs, keeping one-action evidence consumption and callback-error resilience.
- [x] Verify mock/native boundaries and isolated Windows overlay cleanup.

```python
def test_action_preview_omits_typed_content(observed_desktop):
    events = []
    frame = observed_desktop.observe()
    observed_desktop.act(frame.id, 'type', text='Private text', _on_action=lambda f, a: events.append(a))
    assert 'Private text' not in repr(events)
```

### Task 3: Dashboard computer preview

**Owner:** computer_visuals. **Files:** shared computer-use types/parser, sessionController.ts, hook/reducer, new preview component, Stage/ExecutionStatus integration, styles and tests.

**Consumes:** the exact event contract from the spec; authenticated AGENT text streams only.

- [x] Add failing parser/reducer/stream tests for bounds, sender membership, replay, stop/reconnect, stale timestamps and untrusted images.
- [x] Render observed before/after frames, target/pointer overlays, bounded action history and accessible enlarged view using existing theme.
- [x] Run complete frontend tests/typecheck/build and inspect actual Electron synthetic fixture at normal/minimum widths.

```typescript
expect(parseComputerObservation({type:'computer.observation', screenshot_data_url:'https://example.com/image.png'})).toBeNull();
```

### Task 4: Integration and correct tool routing

**Owner:** root. **Files:** src/agent.py, spotify_tools.py, image_pipeline.py, market_report.py, tool bootstrap/policies, new computer_events.py and tests.

**Consumes:** marshalled browser helpers and desktop callback. **Produces:** ordered bounded screenshot publisher, before/after/clear events, actionable scoped browser fallback, correct exposed tool schemas.

- [x] Add failing integration tests for native fallback sequence, no capture after stop/uncertain timeout, screenshot publication ordering/redaction/bounds, and correct tool registry.
- [x] Adapt internal extractors away from driver access; integrate callbacks and stream routing; close the browser worker at agent cleanup.
- [x] Ensure browser numbers refresh after visual input and expose window/focus/zoom tools as required without contradictory policies.

```python
async def test_disconnected_preview_is_not_replayed(fake_room):
    publisher = make_computer_publisher(fake_room)
    publisher({'type':'computer.observation', 'phase':'observed'})
    fake_room.connected = True
    await asyncio.sleep(0)
    assert fake_room.sent == []
```

### Task 5: Full verification and evidence

**Owner:** root with independent review.

- [x] Audit source, dependencies, active skills, Docker and test scripts for complete driver removal; update current docs and metadata.
- [x] Run full backend and dashboard suites, Ruff, TypeScript and production build; run isolated real Playwright and computer/renderer evaluations.
- [x] Run controlled provider fallback routing and real Aaya Sher/Brave recheck; save measured evaluation report and limitations.
- [x] Review actual final diff and each spec requirement; mark complete only with authoritative evidence for every requested outcome.
