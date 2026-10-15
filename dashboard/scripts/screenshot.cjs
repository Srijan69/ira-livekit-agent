// Captures the built dashboard to PNG, for visual review.
//
// Runs the real Electron app against the real build output and the real
// preload bridge, so the stats rings show this machine's actual CPU rather
// than a browser's "--". A separate entry point rather than a flag in
// electron/main.ts: screenshot plumbing has no business in the app's own
// startup path.
//
//   npx electron scripts/screenshot.cjs
//
// Several frames at different delays, because the orb walks through its state
// machine on a loop -- one frame would only ever show one state.
const { app, BrowserWindow, ipcMain } = require("electron");
const { mkdirSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const si = require("systeminformation");

const OUT_DIR = join(__dirname, "..", "shots");

/**
 * A minimal stand-in for main.ts's IPC surface.
 *
 * This harness loads the renderer directly, so none of the app's own handlers
 * are registered and the stats rings would capture as "--" -- making the
 * screenshot a poor guide to how the dashboard actually looks. Reading the
 * host here keeps the capture truthful. Deliberately a few lines of duplicated
 * polling rather than a reason to put screenshot plumbing in main.ts.
 */
async function registerStats(getWindow) {
  const read = async () => {
    const [load, mem, disks, battery] = await Promise.all([
      si.currentLoad(),
      si.mem(),
      si.fsSize(),
      si.battery(),
    ]);
    const primary = disks
      .filter((d) => d.size > 0)
      .sort((a, b) => b.size - a.size)[0];
    return {
      cpu: Math.round(load.currentLoad ?? 0),
      memory: Math.round(((mem.total - mem.available) / mem.total) * 100),
      storage: Math.round(primary?.use ?? 0),
      battery: {
        percent: Math.round(battery.percent ?? 0),
        charging: Boolean(battery.isCharging || battery.acConnected),
        hasBattery: Boolean(battery.hasBattery),
      },
      at: new Date().toISOString(),
    };
  };

  let latest = await read();
  ipcMain.handle("ira:stats:snapshot", () => latest);
  // Window controls are no-ops here; registering them keeps the console clean.
  ipcMain.handle("ira:window:minimize", () => {});
  ipcMain.handle("ira:window:close", () => {});
  ipcMain.handle("ira:window:toggle-always-on-top", () => false);

  setInterval(async () => {
    latest = await read();
    const window = getWindow();
    if (window && !window.isDestroyed()) {
      window.webContents.send("ira:stats", latest);
    }
  }, 2_000);
}

/** [filename, ms after load] -- chosen to land in distinct orb states. */
const FRAMES = [
  ["01-listening.png", 3_200],
  ["02-recognizing.png", 2_000],
  ["03-speaking.png", 3_000],
  ["04-executing.png", 4_000],
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  mkdirSync(OUT_DIR, { recursive: true });

  let window = null;
  await registerStats(() => window);

  window = new BrowserWindow({
    width: 1440,
    height: 960,
    frame: false,
    backgroundColor: "#050B16",
    // Shown rather than offscreen: `backdrop-filter` needs a real compositor,
    // and a hidden window captures the glass panels unblurred -- which would
    // make the screenshot a poor guide to how it actually looks.
    show: true,
    webPreferences: {
      preload: join(__dirname, "..", "out", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  await window.loadFile(join(__dirname, "..", "out", "renderer", "index.html"));

  // Let fonts settle and the entrance stagger finish before the first frame.
  await sleep(1_200);

  for (const [name, delay] of FRAMES) {
    await sleep(delay);
    const image = await window.webContents.capturePage();
    writeFileSync(join(OUT_DIR, name), image.toPNG());
    process.stdout.write(`captured ${name}\n`);
  }

  app.quit();
});
