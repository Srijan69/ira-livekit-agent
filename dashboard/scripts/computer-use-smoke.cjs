// Real Electron compositor QA for the isolated synthetic fixture.
// npx vite build --config scripts/computer-use-qa.config.mts
// npx electron scripts/computer-use-smoke.cjs
const { app, BrowserWindow } = require("electron");
const { join } = require("node:path");
const { mkdirSync, writeFileSync } = require("node:fs");
const assert = require("node:assert/strict");
const out = join(__dirname, "..", "shots");
const page = join(out, "computer-use-renderer", "scripts", "computer-use-qa.html");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
app.whenReady().then(async () => {
  mkdirSync(out, { recursive: true });
  const window = new BrowserWindow({ width: 1440, height: 960, show: true, backgroundColor: "#050B16", webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false } });
  const errors = [];
  window.webContents.on("console-message", (event) => { if (event.level === "error") errors.push(event.message); });
  async function load(mode, width = 1440, height = 960) { window.setSize(width, height); await window.loadFile(page, { query: { mode } }); await delay(650); }
  async function snapshot(name) {
    console.log(`Capturing ${name}`);
    let image;
    try { image = await window.webContents.capturePage(); }
    catch (error) {
      // Chromium can lose a Viz surface briefly after repeated file navigation.
      if (error.message !== "UnknownVizError") throw error;
      await delay(500);
      image = await window.webContents.capturePage();
    }
    writeFileSync(join(out, `computer-use-${name}.png`), image.toPNG());
  }
  const read = () => window.webContents.executeJavaScript(`({ text: document.body.innerText, image: !!document.querySelector('.computer-image img'), target: !!document.querySelector('[data-target-id]'), marker: !!document.querySelector('[data-action-marker]'), error: !!document.querySelector('vite-error-overlay'), overflow: document.documentElement.scrollWidth > innerWidth })`);
  await load("stage");
  let state = await read();
  assert(state.image && state.target && !state.error && !state.overflow, JSON.stringify({ state, errors }));
  await snapshot("after");
  await window.webContents.executeJavaScript(`document.querySelector('.computer-frame-switch button').click()`);
  await delay(100); state = await read(); assert(state.marker);
  await snapshot("before-drag");
  await window.webContents.executeJavaScript(`document.querySelector('button[aria-label="Enlarge observed screen"]').click()`);
  await delay(100);
  assert(await window.webContents.executeJavaScript(`document.activeElement.getAttribute('aria-label') === 'Close observed screen'`));
  await snapshot("enlarged");
  await window.webContents.executeJavaScript(`document.querySelector('[role="dialog"]').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))`);
  await delay(100);
  assert(await window.webContents.executeJavaScript(`!document.querySelector('[role="dialog"]') && document.activeElement.getAttribute('aria-label') === 'Enlarge observed screen'`));
  await load("stale", 1024, 720); state = await read(); assert(state.image && state.text.includes("Last observed") && !state.overflow); await snapshot("stale-narrow");
  await load("offline", 1024, 720); state = await read(); assert(!state.image && state.text.includes("Connect to see")); await snapshot("offline");
  await load("empty", 1440, 960); state = await read(); assert(!state.image && state.text.includes("No screen observation yet")); await snapshot("empty");
  assert.deepEqual(errors, []);
  writeFileSync(join(out, "computer-use-smoke.json"), JSON.stringify({ passed: true, cases: ["after", "before-drag", "enlarged-focus", "escape-focus-return", "stale-narrow", "offline", "empty"], rendererErrors: errors }, null, 2));
  console.log("Computer use Electron QA passed (7 cases)");
  app.quit();
}).catch((error) => { console.error(error); app.exit(1); });
