import { randomBytes } from "node:crypto";
import { dirname, join } from "node:path";

import { BrowserWindow, app, ipcMain, safeStorage, session, shell } from "electron";

import type { ConnectionSaveResult } from "../shared/types";
import { createConnectionStore, type ConnectionStore } from "./connection";

import { findFreePort } from "./ports";
import { createRestartController, prepareDevelopmentRelaunch } from "./restart";
import { ManagedProcess, type ProcessStatus } from "./supervisor";
import { SystemStatsPoller, type SystemStats } from "./system";
import { createTaskProxy } from "./tasks";
import { createSkillProxy } from "./skills";
import { createPersonalMemoryProxy } from "./personalMemory";

// IPC channel names live here so main and preload can't drift apart.
const CHANNEL = {
  appRestart: "ira:app:restart",
  connectionGet: "ira:connection:get",
  connectionSave: "ira:connection:save",
  connectionChanged: "ira:connection:changed",
  statsPush: "ira:stats",
  statsSnapshot: "ira:stats:snapshot",
  windowMinimize: "ira:window:minimize",
  windowClose: "ira:window:close",
  windowToggleTop: "ira:window:toggle-always-on-top",
  sessionToken: "ira:session:token",
  health: "ira:health",
  agentStatus: "ira:agent:status",
  agentStatusPush: "ira:agent:status-push",
  agentRestart: "ira:agent:restart",
  agentLogs: "ira:agent:logs",
  tasksList: "ira:tasks:list",
  tasksCreate: "ira:tasks:create",
  tasksUpdate: "ira:tasks:update",
  tasksDelete: "ira:tasks:delete",
} as const;

let mainWindow: BrowserWindow | null = null;
let poller: SystemStatsPoller | null = null;
let bridge: ManagedProcess | null = null;
let agent: ManagedProcess | null = null;
let bridgePort = 0;
let connectionStore: ConnectionStore;
let configurationRequired = true;
let savingConnection: Promise<unknown> = Promise.resolve();

/**
 * Per-launch secret for the bridge.
 *
 * Loopback binding keeps the network out but not other local processes, and a
 * bridge token is the ability to join IRA's room and talk to her -- which, per
 * this project's own security notes, is the ability to issue desktop
 * commands. This value never reaches the renderer: main proxies every bridge
 * call instead, so a compromised renderer cannot mint tokens on its own.
 */
const bridgeSecret = randomBytes(32).toString("hex");

/**
 * The Python project root -- the directory holding `src/` and `.env.local`.
 *
 * In development the app runs out of `dashboard/`, so the repo is one level
 * up. Overridable for a packaged build, where the layout differs.
 */
function repoRoot(): string {
  return process.env["IRA_REPO_ROOT"] ?? dirname(app.getAppPath());
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 800,
    show: false,
    frame: false,
    title: "IRA",
    icon: join(app.getAppPath(), "assets/ira.ico"),
    // Opaque rather than transparent: the design is a solid dark app, and a
    // transparent frameless window on Windows loses its drop shadow and picks
    // up a set of compositor bugs for no visual gain here. Windows 11 rounds
    // frameless corners on its own.
    backgroundColor: "#050B16",
    titleBarStyle: "hidden",
    webPreferences: {
      preload: join(__dirname, "../preload/preload.js"),
      // All three matter more here than in a typical app: this shell
      // supervises a process that can control the desktop, so the renderer --
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

function send(channel: string, payload: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

/**
 * Legacy external-worker mode cannot receive this desktop profile's keys.
 * Keep it offline and direct credential setup to a normal supervised launch.
 */
const externalAgent = process.env["IRA_EXTERNAL_AGENT"] === "1";

async function startPythonProcesses(): Promise<void> {
  if (!bridgePort) bridgePort = await findFreePort();
  const cwd = repoRoot();
  const credentials = await connectionStore.childEnv();
  configurationRequired = ["GOOGLE_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    .some((field) => !credentials[field]);

  const onStatus = (status: ProcessStatus) => send(CHANNEL.agentStatusPush, status);

  bridge = new ManagedProcess(
    "bridge",
    "uv",
    ["run", "--no-env-file", "python", "src/bridge.py", "--port", String(bridgePort)],
    {
      cwd,
      env: { ...credentials, IRA_BRIDGE_SECRET: bridgeSecret },
      // Our own log line, printed once the socket is actually listening.
      readyPattern: /bridge listening/,
      onStatus,
    },
  );

  if (!externalAgent) {
    // `dev` rather than `console`: console runs the job in-process and
    // expects a terminal to talk to. `dev` registers a worker that joins the
    // rooms the dashboard creates.
    agent = new ManagedProcess("agent", "uv", ["run", "--no-env-file", "python", "src/agent.py", "dev"], {
      cwd,
      env: credentials,
      // The SDK's own line once the worker can take a dispatch. Joining a
      // room before this is how IRA ends up never arriving: the room's
      // dispatch is created at join, with no worker yet to take it.
      readyPattern: /registered worker/,
      onStatus,
    });
  }

  bridge.start();
  if (!configurationRequired) agent?.start();
}

async function restartPythonProcesses(): Promise<void> {
  agent?.stop();
  bridge?.stop();
  await startPythonProcesses();
}

/**
 * Grant the microphone and nothing else.
 *
 * Electron approves every permission request by default. This renderer
 * displays text IRA read off arbitrary web pages, so anything it doesn't need
 * -- camera, geolocation, notifications, clipboard -- is refused outright.
 */
function restrictPermissions(): void {
  const audioOnly = (mediaTypes?: readonly string[]) =>
    (mediaTypes ?? []).every((type) => type === "audio");

  session.defaultSession.setPermissionRequestHandler(
    (_webContents, permission, callback, details) => {
      const mediaTypes = "mediaTypes" in details ? details.mediaTypes : undefined;
      callback(permission === "media" && audioOnly(mediaTypes));
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission) => permission === "media",
  );
}

async function callBridge(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`http://127.0.0.1:${bridgePort}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      // The secret is added here, in main. The renderer never sees it.
      "X-IRA-Bridge-Secret": bridgeSecret,
      ...(init?.headers ?? {}),
    },
  });
}

function registerIpc(): void {
  const requireMainWindow = (event: Electron.IpcMainInvokeEvent) => {
    if (event.sender !== mainWindow?.webContents) throw new Error("Unknown window");
  };
  ipcMain.handle(CHANNEL.connectionGet, (event) => {
    requireMainWindow(event);
    return connectionStore.get();
  });
  ipcMain.handle(CHANNEL.connectionSave, (event, input: unknown) => {
    requireMainWindow(event);
    const save = async (): Promise<ConnectionSaveResult> => {
      // An independently launched worker cannot receive this profile's keys.
      if (externalAgent) {
        return { error: "Close IRA and launch it without IRA_EXTERNAL_AGENT to connect using your saved keys." };
      }
      const result = await connectionStore.save(input);
      if ("error" in result) return result;
      try {
        await restartPythonProcesses();
        send(CHANNEL.connectionChanged, undefined);
      } catch {
        return { error: "Keys were saved, but IRA could not restart. Close and reopen IRA to reconnect." };
      }
      return result;
    };
    // Keep persistence and the corresponding process restart in one order.
    const result = savingConnection.then(save, save);
    savingConnection = result.catch(() => {});
    return result;
  });
  const restartApp = createRestartController({
    flushStorage: () => session.defaultSession.flushStorageData(),
    prepareRelaunch: async () => {
      const devUrl = process.env["ELECTRON_RENDERER_URL"];
      if (devUrl && !app.isPackaged) {
        await prepareDevelopmentRelaunch(app.getAppPath(), devUrl);
      } else {
        app.relaunch();
      }
    },
    // Let the IPC acknowledgement reach the renderer before tearing it down.
    quit: () => { setTimeout(() => app.quit(), 50); },
  });
  ipcMain.handle(CHANNEL.appRestart, (event) => {
    if (event.sender !== mainWindow?.webContents) throw new Error("Unknown window");
    return restartApp();
  });

  const tasks = createTaskProxy(callBridge);
  ipcMain.handle(CHANNEL.tasksList, (_event, limit) => tasks.list(limit));
  ipcMain.handle(CHANNEL.tasksCreate, (_event, input) => tasks.create(input));
  ipcMain.handle(CHANNEL.tasksUpdate, (_event, id, patch) => tasks.update(id, patch));
  ipcMain.handle(CHANNEL.tasksDelete, (_event, id) => tasks.delete(id));

  const personalMemory = createPersonalMemoryProxy(callBridge, () => send("ira:personal-memory:changed", undefined));
  for (const operation of ["profile", "saveProfile", "resetProfile", "settings", "saveSettings", "list", "status", "deleteMemory", "clearMemory"] as const) {
    ipcMain.handle(`ira:personal-memory:${operation}`, (event, ...args: unknown[]) => {
      requireMainWindow(event);
      return (personalMemory[operation] as (...input: unknown[]) => unknown)(...args);
    });
  }

  const skills = createSkillProxy(callBridge);
  ipcMain.handle("ira:skills:list", (_event, archived) => skills.list(archived));
  ipcMain.handle("ira:skills:detail", (_event, slug, version) => skills.detail(slug, version));
  ipcMain.handle("ira:skills:history", (_event, limit) => skills.history(limit));
  ipcMain.handle("ira:skills:suggestions", () => skills.suggestions());
  ipcMain.handle("ira:skills:pin", (_event, slug, pinned) => skills.pin(slug, pinned));
  ipcMain.handle("ira:skills:archive", (_event, slug) => skills.archive(slug));
  ipcMain.handle("ira:skills:restore", (_event, slug) => skills.restore(slug));

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

  ipcMain.handle(CHANNEL.sessionToken, async (_event, room?: string) => {
    if (configurationRequired || externalAgent) {
      return { error: "Open Settings > Connection and Keys to connect IRA with your own credentials." };
    }
    try {
      const response = await callBridge("/token", {
        method: "POST",
        body: JSON.stringify({ identity: "boss", room }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        return { error: body.error ?? `bridge returned ${response.status}` };
      }
      return await response.json();
    } catch (error) {
      // The bridge not being up yet is the common case during launch, so this
      // returns a message rather than throwing into the renderer.
      return { error: `bridge unreachable: ${(error as Error).message}` };
    }
  });

  ipcMain.handle(CHANNEL.health, async () => {
    try {
      const response = await callBridge("/health", { method: "GET" });
      return await response.json();
    } catch (error) {
      return { bridge: "down", error: (error as Error).message };
    }
  });

  ipcMain.handle(CHANNEL.agentStatus, () => ({
    supervised: !externalAgent,
    configurationRequired: configurationRequired || externalAgent,
    agent: agent?.status() ?? null,
    bridge: bridge?.status() ?? null,
  }));

  ipcMain.handle(CHANNEL.agentLogs, () => ({
    agent: agent?.recentLogs() ?? [],
    bridge: bridge?.recentLogs() ?? [],
  }));

  ipcMain.handle(CHANNEL.agentRestart, (event) => {
    requireMainWindow(event);
    const restart = async () => {
      await restartPythonProcesses();
      send(CHANNEL.connectionChanged, undefined);
    };
    const result = savingConnection.then(restart, restart);
    savingConnection = result.catch(() => {});
    return result;
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

  void app.whenReady().then(async () => {
    connectionStore = createConnectionStore({
      path: join(app.getPath("userData"), "connection.json"),
      encryption: safeStorage,
    });
    restrictPermissions();
    registerIpc();
    // Before the window: the renderer's first status query must find both
    // processes constructed, or it would read "no agent" as "unsupervised"
    // and connect before IRA's worker exists.
    await startPythonProcesses();
    mainWindow = createWindow();

    poller = new SystemStatsPoller((stats: SystemStats) => {
      send(CHANNEL.statsPush, stats);
    });
    bindVisibility(mainWindow, poller);
    poller.start();

    // End-to-end probe: scripts/e2e-probe.cjs, loaded only when
    // IRA_E2E_PROBE names it and never in a packaged build. It exercises the
    // real main process -- supervisor, bridge, agent -- which a harness that
    // loads only the renderer cannot.
    const probe = process.env["IRA_E2E_PROBE"];
    if (probe && !app.isPackaged) {
      require(probe)({ app, window: mainWindow, agent, bridge });
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createWindow();
        if (poller) bindVisibility(mainWindow, poller);
      }
    });
  });

  // Both children are killed on the way out. An orphaned agent would hold the
  // microphone and keep answering with no window to show for it.
  app.once("before-quit", () => {
    poller?.stop();
    bridge?.stop();
    agent?.stop();
  });

  app.on("window-all-closed", () => {
    poller?.stop();
    if (process.platform !== "darwin") app.quit();
  });
}
