// Fresh-profile smoke check against the built Electron main, preload, renderer
// and Python bridge. Uses no provider credentials or voice connection.
// Run after npm run build: node_modules/.bin/electron scripts/connection-smoke.cjs
const assert = require("node:assert/strict");
const { mkdtempSync, mkdirSync, writeFileSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { resolve, join } = require("node:path");
const { app, BrowserWindow } = require("electron");

const profile = mkdtempSync(join(tmpdir(), "ira-connection-smoke-"));
app.setName("ira-connection-smoke");
app.setPath("userData", profile);
process.env.IRA_REPO_ROOT = resolve(__dirname, "../..");
process.env.IRA_TASKS_DB = join(profile, "tasks.db");
process.env.IRA_MEMORIES_DIR = join(profile, "memories");
process.env.IRA_EXTERNAL_AGENT = "0";
delete process.env.IRA_E2E_PROBE;
delete process.env.ELECTRON_RENDERER_URL;
require("../out/main/main.js");

const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
async function waitFor(read, predicate) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const value = await read();
    if (predicate(value)) return value;
    await sleep(100);
  }
  throw new Error("Fresh-profile smoke check timed out.");
}

app.whenReady().then(async () => {
  let failed = false;
  try {
    const window = await waitFor(() => BrowserWindow.getAllWindows()[0], Boolean);
    const read = (code) => window.webContents.executeJavaScript(code);
    await waitFor(() => read("Boolean(window.ira && Array.from(document.querySelectorAll('nav button')).some(button => button.textContent.trim() === 'Settings'))").catch(() => false), Boolean);
    const settings = await read("window.ira.connection.get()");
    assert.equal(settings.configured, false);
    assert.equal(settings.hasGoogleApiKey, false);
    assert.equal(settings.hasLivekitApiKey, false);
    assert.equal(settings.hasLivekitApiSecret, false);
    const status = await read("window.ira.agent.status()");
    assert.equal(status.configurationRequired, true);
    assert.equal(status.agent.state, "stopped");
    const health = await waitFor(() => read("window.ira.session.health()"), (value) => value.bridge === "ok");
    assert.deepEqual(health.config, { livekit_url: false, api_key: false, api_secret: false, google_api_key: false });
    const token = await read("window.ira.session.token()");
    assert.match(token.error, /Connection and Keys/);
    await read("Array.from(document.querySelectorAll('nav button')).find(button => button.textContent.trim() === 'Settings').click()");
    await waitFor(() => read("Boolean(document.querySelector('#settings-tab-connection'))"), Boolean);
    await read("document.querySelector('#settings-tab-connection').click()");
    await waitFor(() => read("Boolean(document.querySelector('#connection-googleApiKey'))"), Boolean);
    const fields = await read("Array.from(document.querySelectorAll('.connection-field input')).map(input => ({ type: input.type, value: input.value }))");
    assert.equal(fields.length, 4);
    assert.equal(fields.filter((field) => field.type === "password").length, 3);
    assert.equal(fields.every((field) => field.value === ""), true);
    if (process.env.IRA_CONNECTION_SHOTS === "1") {
      const output = resolve(__dirname, "../shots");
      mkdirSync(output, { recursive: true });
      for (const [width, height] of [[1440, 960], [1180, 800]]) {
        window.setSize(width, height);
        await sleep(250);
        assert.equal(await read("document.querySelector('.settings-page').scrollWidth <= document.querySelector('.settings-page').clientWidth"), true);
        const screenshot = await window.webContents.capturePage();
        writeFileSync(join(output, `connection-keys-${width}.png`), screenshot.toPNG());
      }
    }
    console.log("Connection smoke passed: fresh profile, real bridge with no inherited keys, offline voice, four settings fields and masked secrets.");
  } catch (error) {
    failed = true;
    console.error(error.message);
  } finally {
    // Hold quit briefly so main's before-quit listener stops the complete
    // Python tree, then exit with the smoke result. Chromium holds profile
    // files open until exit; leave this temporary directory for OS cleanup.
    app.once("before-quit", (event) => {
      event.preventDefault();
      setTimeout(() => app.exit(failed ? 1 : 0), 500);
    });
    app.quit();
  }
});
