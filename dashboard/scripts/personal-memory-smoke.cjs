// Actual Electron main/preload/renderer UI smoke with synthetic bridge data.
// No Python workers, provider requests, saved keys, or real memory files.
// Run after npm run build: node_modules/.bin/electron scripts/personal-memory-smoke.cjs
const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const { EventEmitter } = require("node:events");
const {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const { app, BrowserWindow, session } = require("electron");

const temporary = mkdtempSync(join(tmpdir(), "ira-personal-memory-smoke-"));
const statePath = join(temporary, "synthetic-personal-memory.json");
const output = resolve(__dirname, "../../artifacts");
app.setName("ira-personal-memory-smoke");
app.setPath("userData", temporary);
process.env.IRA_REPO_ROOT = resolve(__dirname, "../..");
process.env.IRA_TASKS_DB = join(temporary, "tasks.db");
process.env.IRA_MEMORIES_DIR = join(temporary, "memories");
process.env.IRA_EXTERNAL_AGENT = "0";
for (const name of [
  "GOOGLE_API_KEY",
  "LIVEKIT_URL",
  "LIVEKIT_API_KEY",
  "LIVEKIT_API_SECRET",
  "IRA_E2E_PROBE",
  "ELECTRON_RENDERER_URL",
])
  delete process.env[name];

const blankProfile = {
  name: "",
  age: null,
  occupation: "",
  preferences: "",
  revision: 0,
  updated_at: null,
};
const timestamp = "2026-11-06T10:00:00Z";
const initial = {
  profile: blankProfile,
  settings: {
    enabled: true,
    context_budget: 16000,
    summary_trigger: 12000,
    recent_turns: 12,
    summarizer_model: "gemini-3.8-flash",
    revision: 0,
  },
  memories: [
    {
      id: "fact-astronomy",
      kind: "fact",
      content: "Enjoys astronomy and planning observations of Mars.",
      source: "explicit",
      created_at: timestamp,
    },
    {
      id: "fact-coffee",
      kind: "fact",
      content: "Prefers decaffeinated coffee after lunch.",
      source: "explicit",
      created_at: timestamp,
    },
    {
      id: "journal-observations",
      kind: "conversation",
      content:
        "Discussed checking the weather before the next evening observation.",
      source: "conversation",
      created_at: timestamp,
    },
  ],
  checkpoint: {
    id: "checkpoint-smoke",
    conversation_id: "conversation-smoke",
    through_seq: 24,
    memory_revision: 0,
    created_at: timestamp,
    summary: {
      overview:
        "Planned an evening astronomy session and compared observation dates. The next step is to choose a clear evening and prepare the equipment checklist.",
      facts: [
        {
          content: "Astronomy observations are planned for the evening.",
          source_item_ids: ["synthetic-turn-1"],
        },
      ],
      decisions: [
        {
          content: "Use metric units in the equipment checklist.",
          source_item_ids: ["synthetic-turn-2"],
        },
      ],
      open_threads: [
        {
          content:
            "Choose the next observation date after checking the weather.",
          source_item_ids: ["synthetic-turn-3"],
        },
      ],
      outcomes: [],
      questions: [],
    },
  },
  memory_revision: 0,
  status: { state: "collecting" },
};
writeFileSync(statePath, JSON.stringify(initial));
function loadState() {
  return JSON.parse(readFileSync(statePath, "utf8"));
}
function saveState(state) {
  writeFileSync(statePath, JSON.stringify(state));
}
let statusFailure = false;
const requests = [];
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// Exercise the built main's real proxy and preload IPC without a real backend.
global.fetch = async (address, init = {}) => {
  const url = new URL(String(address));
  assert.equal(
    url.hostname,
    "127.0.0.1",
    "Smoke must never make a provider request.",
  );
  assert.ok(
    new Headers(init.headers).get("X-IRA-Bridge-Secret"),
    "Built main must authenticate bridge calls.",
  );
  const method = init.method || "GET";
  const path = url.pathname;
  requests.push({ method, path });
  const state = loadState();
  const body = init.body ? JSON.parse(init.body) : {};
  if (path === "/health")
    return json({
      bridge: "ok",
      config: {
        livekit_url: false,
        api_key: false,
        api_secret: false,
        google_api_key: false,
      },
    });
  if (path === "/tasks") return json({ tasks: [] });
  if (path === "/profile" && method === "GET") return json(state.profile);
  if (path === "/profile" && method === "PATCH") {
    if (body.expected_revision !== state.profile.revision)
      return json({ error: "Profile changed elsewhere." }, 409);
    state.profile = {
      name: body.name,
      age: body.age,
      occupation: body.occupation,
      preferences: body.preferences,
      revision: state.profile.revision + 1,
      updated_at: timestamp,
    };
    saveState(state);
    return json(state.profile);
  }
  if (path === "/profile/reset" && method === "POST") {
    state.profile = {
      ...blankProfile,
      revision: state.profile.revision + 1,
      updated_at: timestamp,
    };
    saveState(state);
    return json(state.profile);
  }
  if (path === "/memory/settings" && method === "GET")
    return json(state.settings);
  if (path === "/memory/settings" && method === "PATCH") {
    if (body.expected_revision !== state.settings.revision)
      return json({ error: "Memory settings changed elsewhere." }, 409);
    const { expected_revision, ...patch } = body;
    state.settings = {
      ...state.settings,
      ...patch,
      revision: state.settings.revision + 1,
    };
    saveState(state);
    return json(state.settings);
  }
  if (path === "/memory/status") {
    if (statusFailure)
      return json(
        { error: "Synthetic memory bridge unavailable. Try again." },
        503,
      );
    return json({
      settings: state.settings,
      memory_revision: state.memory_revision,
      checkpoint: state.checkpoint,
      status: state.status,
    });
  }
  if (path === "/memory" && method === "GET") {
    const query = (url.searchParams.get("query") || "").toLowerCase();
    const limit = Number(url.searchParams.get("limit") || 100);
    return json({
      memories: state.memories
        .filter((record) => record.content.toLowerCase().includes(query))
        .slice(0, limit),
    });
  }
  if (path === "/memory/clear" && method === "POST") {
    state.memories = [];
    state.checkpoint = null;
    state.memory_revision += 1;
    state.status = { state: "collecting" };
    saveState(state);
    return json({ cleared: true });
  }
  if (path.startsWith("/memory/") && method === "DELETE") {
    const id = decodeURIComponent(path.slice("/memory/".length));
    const previous = state.memories.length;
    state.memories = state.memories.filter((record) => record.id !== id);
    state.memory_revision += 1;
    saveState(state);
    return json({ deleted: state.memories.length < previous });
  }
  throw new Error(`Unexpected synthetic bridge request: ${method} ${path}`);
};

// No Python process may start or read the repository's memory/environment.
// A fake child supplies the normal supervisor readiness event to the real main.
childProcess.spawn = (command, args, options) => {
  assert.equal(command, "uv");
  assert.ok(
    args.includes("src/bridge.py"),
    "Voice worker must remain stopped without credentials.",
  );
  for (const name of [
    "GOOGLE_API_KEY",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
  ])
    assert.equal(options.env[name], "");
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.pid = undefined;
  child.exitCode = null;
  queueMicrotask(() => {
    child.emit("spawn");
    child.stdout.emit("data", "bridge listening (synthetic UI smoke)\n");
  });
  return child;
};

require("../out/main/main.js");
const sleep = (milliseconds) =>
  new Promise((complete) => setTimeout(complete, milliseconds));
async function waitFor(read, predicate, label = "condition") {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const value = await read();
    if (predicate(value)) return value;
    await sleep(80);
  }
  throw new Error(`Personal memory smoke timed out waiting for ${label}.`);
}

app.whenReady().then(async () => {
  let failed = false;
  try {
    // Cancel renderer network traffic too; this smoke is entirely offline.
    session.defaultSession.webRequest.onBeforeRequest(
      { urls: ["http://*/*", "https://*/*"] },
      (_details, callback) => callback({ cancel: true }),
    );
    mkdirSync(output, { recursive: true });
    const window = await waitFor(
      () => BrowserWindow.getAllWindows()[0],
      Boolean,
      "Electron window",
    );
    const read = (code) => window.webContents.executeJavaScript(code);
    const click = (selector) =>
      read(
        `(() => { const element = document.querySelector(${JSON.stringify(selector)}); if (!element || element.disabled) throw new Error('Missing or disabled control'); element.focus(); element.click(); })()`,
      );
    const clickText = (text) =>
      read(
        `(() => { const button = Array.from(document.querySelectorAll('button')).find(button => !button.closest('[hidden]') && button.textContent.trim() === ${JSON.stringify(text)}); if (!button || button.disabled) throw new Error('Missing or disabled button'); button.focus(); button.click(); })()`,
      );
    const change = (selector, value) =>
      read(
        `(() => { const input = document.querySelector(${JSON.stringify(selector)}); const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); })()`,
      );
    const visibleText = () =>
      read(
        "document.querySelector('[role=tabpanel]:not([hidden])')?.innerText || ''",
      );
    const ready = (name) =>
      waitFor(
        () =>
          read(
            `Array.from(document.querySelectorAll('button')).some(button => !button.closest('[hidden]') && button.textContent.trim() === ${JSON.stringify(name)} && !button.disabled)`,
          ),
        Boolean,
        name,
      );
    const notify = () =>
      window.webContents.send("ira:personal-memory:changed", undefined);
    const captured = [];
    async function screenshot(name, width, height, selector) {
      window.setSize(width, height);
      await sleep(250);
      await read(
        selector
          ? `document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'start' })`
          : "document.querySelector('.settings-page').scrollTop = 0",
      );
      await sleep(200);
      window.webContents.sendInputEvent({ type: "mouseMove", x: 1, y: 1 });
      await read(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
      );
      const layout = await read(
        "(() => { const page = document.querySelector('.settings-page'); const panel = document.querySelector('[role=tabpanel]:not([hidden])'); const rectangle = panel.getBoundingClientRect(); return { width: innerWidth, height: innerHeight, selectedTabMatchesPanel: document.querySelector('[role=tab][aria-selected=true]').getAttribute('aria-controls') === panel.id, horizontalOverflow: page.scrollWidth > page.clientWidth, panelOutsidePage: rectangle.left < page.getBoundingClientRect().left || rectangle.right > page.getBoundingClientRect().right + 1 }; })()",
      );
      assert.equal(layout.selectedTabMatchesPanel, true);
      assert.equal(
        layout.horizontalOverflow,
        false,
        `${name} has horizontal overflow`,
      );
      assert.equal(
        layout.panelOutsidePage,
        false,
        `${name} exceeds the settings page`,
      );
      const image = await window.webContents.capturePage();
      const path = join(output, `profile-memory-${name}-${width}.png`);
      writeFileSync(path, image.toPNG());
      captured.push(path);
      console.log(
        `Captured ${name}: ${width} × ${height}, no horizontal overflow.`,
      );
    }

    await waitFor(
      () =>
        read(
          "Boolean(window.ira?.personalMemory && document.querySelector('nav'))",
        ).catch(() => false),
      Boolean,
      "preload and dashboard",
    );
    const connection = await read("window.ira.connection.get()");
    assert.equal(connection.configured, false);
    assert.equal(connection.hasGoogleApiKey, false);
    assert.equal(
      (await read("window.ira.agent.status()")).agent.state,
      "stopped",
    );
    await clickText("Settings");
    await click("#settings-tab-profile");
    await ready("Save profile");
    assert.match(
      await read("document.querySelector('nav').innerText"),
      /Your profile/,
    );
    assert.equal(
      await read("document.querySelector('#profile-name').value"),
      "",
    );
    await change("#profile-name", "Ada Lovelace");
    await change("#profile-age", "36");
    await change("#profile-occupation", "Research engineer");
    await change(
      "#profile-preferences",
      "Use concise explanations and metric units. I enjoy astronomy and practical examples.",
    );
    await clickText("Save profile");
    await waitFor(
      visibleText,
      (text) => text.includes("Profile saved."),
      "profile save confirmation",
    );
    assert.equal(loadState().profile.name, "Ada Lovelace");
    assert.equal(loadState().profile.age, 36);
    assert.match(
      await read("document.querySelector('nav').innerText"),
      /AL\s+Ada Lovelace/,
    );
    for (const [width, height] of [
      [1440, 960],
      [1180, 800],
    ]) {
      await screenshot("profile", width, height);
      await screenshot(
        "profile-actions",
        width,
        height,
        "#settings-panel-profile .connection-save-row",
      );
    }
    await change("#profile-age", "");
    await clickText("Save profile");
    await waitFor(
      () => loadState().profile.age,
      (age) => age === null,
      "blank optional age saved as null",
    );
    await ready("Save profile");

    // Keyboard selection traverses all four Settings sections.
    await read(
      "document.querySelector('#settings-tab-profile').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))",
    );
    assert.equal(
      await read("document.activeElement.id"),
      "settings-tab-memory",
    );
    await ready("Save memory settings");
    assert.equal(
      await read("document.querySelector('#memory-enabled').checked"),
      true,
    );
    assert.match(await visibleText(), /Planned an evening astronomy session/);
    assert.match(
      await read(
        "document.querySelector('[aria-label=\"Context memory status\"]').innerText",
      ),
      /Collecting/,
    );
    for (const [width, height] of [
      [1440, 960],
      [1180, 800],
    ]) {
      await screenshot("memory", width, height);
      await screenshot("details", width, height, ".memory-summary");
    }

    await read("document.querySelector('.memory-advanced').open = true");
    await change("#memory-context_budget", "20000");
    await change("#memory-summary_trigger", "15000");
    await change("#memory-recent_turns", "16");
    await change("#memory-summarizer_model", "gemini-3.8-flash");
    await clickText("Save memory settings");
    await waitFor(
      visibleText,
      (text) => text.includes("Memory settings saved."),
      "settings save confirmation",
    );
    assert.equal(loadState().settings.context_budget, 20000);
    assert.equal(loadState().settings.summary_trigger, 15000);
    assert.equal(loadState().settings.recent_turns, 16);
    for (const [width, height] of [
      [1440, 960],
      [1180, 800],
    ]) {
      await screenshot("advanced", width, height);
      await screenshot(
        "advanced-fields",
        width,
        height,
        ".memory-advanced-grid",
      );
    }
    await click("#memory-enabled");
    await clickText("Save memory settings");
    await waitFor(
      () => loadState().settings.enabled,
      (value) => value === false,
      "automatic memory disabled",
    );
    await waitFor(
      () =>
        read(
          "document.querySelector('[aria-label=\"Context memory status\"]').innerText",
        ),
      (text) => text.includes("Disabled"),
      "disabled status",
    );
    await ready("Save memory settings");
    await click("#memory-enabled");
    await clickText("Save memory settings");
    await waitFor(
      () => loadState().settings.enabled,
      (value) => value === true,
      "automatic memory enabled",
    );
    for (const [state, label] of [
      ["summarizing", "Summarizing"],
      ["waiting", "Waiting to compact"],
      ["waiting_to_compact", "Waiting to compact"],
      ["compacting", "Compacting"],
      ["error", "Needs attention"],
      ["restored", "Context restored"],
      ["ready", "Ready"],
    ]) {
      const saved = loadState();
      saved.status = {
        state,
        ...(state === "error"
          ? { last_error: "Synthetic summarizer timeout." }
          : {}),
      };
      saveState(saved);
      notify();
      const text = await waitFor(
        () =>
          read(
            "document.querySelector('[aria-label=\"Context memory status\"]').innerText",
          ),
        (text) => text.startsWith(label),
        `${state} status`,
      );
      if (state === "restored") {
        assert.match(text, /Waiting for the provider/);
        assert.doesNotMatch(text, /provider activity confirmed/);
        await screenshot("restored", 1440, 960, ".memory-context-status");
      }
      if (state === "ready")
        assert.match(text, /Current provider activity confirmed/);
      if (state === "error")
        assert.match(text, /Synthetic summarizer timeout.*IRA will retry/s);
    }
    statusFailure = true;
    await clickText("Refresh status");
    await waitFor(
      () =>
        read(
          "document.querySelector('[aria-label=\"Context memory status\"]').innerText",
        ),
      (text) => text.includes("Session status unavailable"),
      "unavailable status",
    );
    assert.match(await visibleText(), /Planned an evening astronomy session/);
    statusFailure = false;
    await clickText("Refresh status");
    await waitFor(
      () =>
        read(
          "document.querySelector('[aria-label=\"Context memory status\"]').innerText",
        ),
      (text) => text.startsWith("Ready"),
      "status recovery",
    );

    await change("#memory-search", "coffee");
    await waitFor(
      () =>
        read(
          "Array.from(document.querySelectorAll('.memory-records li')).map(row => row.innerText)",
        ),
      (rows) => rows.length === 1 && rows[0].includes("decaffeinated coffee"),
      "memory search",
    );
    await click(".memory-records button");
    await waitFor(
      () => loadState().memories.length,
      (count) => count === 2,
      "memory deletion",
    );
    await waitFor(
      visibleText,
      (text) => text.includes("Memory deleted."),
      "deletion confirmation",
    );
    assert.equal(loadState().profile.name, "Ada Lovelace");
    await change("#memory-search", "no-matching-synthetic-detail");
    await waitFor(
      visibleText,
      (text) => text.includes("No memories match"),
      "empty search",
    );
    await change("#memory-search", "");
    await waitFor(
      () => read("document.querySelectorAll('.memory-records li').length"),
      (count) => count === 2,
      "remaining memories",
    );
    await clickText("Clear conversation memory");
    assert.equal(
      loadState().memories.length,
      2,
      "Clear must wait for its separate confirmation.",
    );
    await clickText("Confirm clear conversation memory");
    await waitFor(
      visibleText,
      (text) => text.includes("Conversation memory cleared."),
      "memory clear confirmation",
    );
    assert.equal(loadState().memories.length, 0);
    assert.equal(loadState().checkpoint, null);
    assert.equal(loadState().profile.name, "Ada Lovelace");
    await screenshot("cleared", 1180, 800, ".memory-summary");

    await click("#settings-tab-profile");
    await clickText("Reset profile");
    assert.equal(
      loadState().profile.name,
      "Ada Lovelace",
      "Reset must wait for its separate confirmation.",
    );
    await clickText("Confirm reset profile");
    await waitFor(
      visibleText,
      (text) => text.includes("Profile reset."),
      "profile reset confirmation",
    );
    const reset = loadState().profile;
    assert.equal(reset.name, "");
    assert.equal(reset.age, null);
    assert.equal(reset.occupation, "");
    assert.equal(reset.preferences, "");
    assert.match(
      await read("document.querySelector('nav').innerText"),
      /Your profile/,
    );
    assert.equal(
      await read("document.querySelector('#profile-age').value"),
      "",
    );
    await screenshot("reset", 1180, 800);

    window.webContents.reload();
    await waitFor(
      () => read("Boolean(document.querySelector('nav'))").catch(() => false),
      Boolean,
      "renderer reload",
    );
    await clickText("Settings");
    await click("#settings-tab-profile");
    await ready("Save profile");
    assert.equal(
      await read("document.querySelector('#profile-name').value"),
      "",
    );
    assert.match(
      await read("document.querySelector('nav').innerText"),
      /Your profile/,
    );
    await click("#settings-tab-memory");
    await ready("Save memory settings");
    assert.equal(
      await read("document.querySelector('#memory-context_budget').value"),
      "20000",
    );
    await waitFor(
      visibleText,
      (text) => text.includes("No remembered details yet."),
      "cleared memory after reload",
    );
    assert.equal(
      requests.some(
        (request) => request.method === "PATCH" && request.path === "/profile",
      ),
      true,
    );
    assert.equal(
      requests.some(
        (request) =>
          request.method === "DELETE" && request.path === "/memory/fact-coffee",
      ),
      true,
    );
    console.log(
      `Personal memory smoke passed: actual built Electron main/preload/renderer, synthetic authenticated bridge, profile save/reset/sidebar, settings, all compaction labels, status failure/recovery, summary, search/delete/clear, renderer reload, ${captured.length} screenshots.`,
    );
  } catch (error) {
    failed = true;
    console.error(error.stack || error.message);
  } finally {
    // Leave only isolated synthetic app data for OS cleanup; never remove user data.
    app.once("before-quit", (event) => {
      event.preventDefault();
      setTimeout(() => app.exit(failed ? 1 : 0), 200);
    });
    app.quit();
  }
});
