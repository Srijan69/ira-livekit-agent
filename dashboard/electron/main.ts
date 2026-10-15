import { join } from "node:path";

import { BrowserWindow, app, ipcMain, shell } from "electron";

import { SystemStatsPoller, type SystemStats } from "./system";

// IPC channel names live here so main and preload can't drift apart.
const CHANNEL = {
  statsPush: "ira:stats",
  statsSnapshot: "ira:stats:snapshot",
  windowMinimize: "ira:window:minimize",
  windowClose: "ira:window:close",
  windowToggleTop: "ira:window:toggle-always-on-top",
} as const;

let mainWindow: BrowserWindow | null = null;
let poller: SystemStatsPoller | null = null;

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 800,
    show: false,
    frame: false,
    // Opaque rather than transparent: the design is a solid dark app, and a
    // transparent frameless window on Windows loses its drop shadow and picks
    // up a set of compositor bugs for no visual gain here. Windows 11 rounds
    // frameless corners on its own.
    backgroundColor: "#050B16",
    titleBarStyle: "hidden",
    webPreferences: {
      preload: join(__dirname, "../preload/preload.js"),
      // All three matter more here than in a typical app: this shell will
      // supervise a process that can control the desktop, so the renderer --
      // which displays text fetched from the web via IRA's browser tools --
      // gets no Node reach at all.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  // Paint before showing. A frameless window that appears empty and then fills
  // in reads as a broken app on launch.
  window.once("ready-to-show", () => window.show());

  // Any target="_blank" or window.open goes to the real browser. IRA has her
  // own Chrome for browsing; the dashboard is not a web view.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  const devUrl = process.env["ELECTRON_RENDERER_URL"];
  if (devUrl) {
    void window.loadURL(devUrl);
  } else {
    void window.loadFile(join(__dirname, "../renderer/index.html"));
  }

  return window;
}

/**
 * Stats run only while the window can actually be seen.
 *
 * Section 3's performance rule, enforced in the process that owns the cost:
 * the renderer pausing its animations doesn't help if main is still querying
 * the disk every two seconds behind a minimized window.
 */
function bindVisibility(window: BrowserWindow, statsPoller: SystemStatsPoller): void {
  const resume = () => statsPoller.start();
  const suspend = () => statsPoller.stop();

  window.on("focus", resume);
  window.on("restore", resume);
  window.on("show", resume);
  window.on("blur", suspend);
  window.on("minimize", suspend);
  window.on("hide", suspend);
  window.on("closed", suspend);
}

function registerIpc(): void {
  ipcMain.handle(CHANNEL.statsSnapshot, () => poller?.snapshot() ?? null);

  ipcMain.handle(CHANNEL.windowMinimize, () => {
    mainWindow?.minimize();
  });

  ipcMain.handle(CHANNEL.windowClose, () => {
    mainWindow?.close();
  });

  ipcMain.handle(CHANNEL.windowToggleTop, () => {
    if (!mainWindow) return false;
    const next = !mainWindow.isAlwaysOnTop();
    mainWindow.setAlwaysOnTop(next);
    return next;
  });
}

// One window only. A second IRA dashboard would mean a second voice session
// talking over the first.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  void app.whenReady().then(() => {
    registerIpc();
    mainWindow = createWindow();

    poller = new SystemStatsPoller((stats: SystemStats) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send(CHANNEL.statsPush, stats);
      }
    });
    bindVisibility(mainWindow, poller);
    poller.start();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createWindow();
        if (poller) bindVisibility(mainWindow, poller);
      }
    });
  });

  app.on("window-all-closed", () => {
    poller?.stop();
    if (process.platform !== "darwin") app.quit();
  });
}
