// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionInput } from "../shared/types";

const h = vi.hoisted(() => {
  const credentials = {
    GOOGLE_API_KEY: "saved-google-key",
    LIVEKIT_URL: "wss://saved.example",
    LIVEKIT_API_KEY: "saved-livekit-key",
    LIVEKIT_API_SECRET: "saved-livekit-secret",
  };
  return {
    credentials,
    handlers: new Map<string, (...args: any[]) => any>(),
    windows: [] as any[],
    processes: [] as any[],
    configured: false,
    environmentUnavailable: false,
    rejected: false,
    lifecycle: [] as string[],
    saveCalls: 0,
  };
});

vi.mock("electron", () => {
  class BrowserWindow {
    webContents = { send: vi.fn(), setWindowOpenHandler: vi.fn() };
    constructor() { h.windows.push(this); }
    once() {}
    on() {}
    show() {}
    isDestroyed() { return false; }
    loadFile() { return Promise.resolve(); }
    static getAllWindows() { return h.windows; }
  }
  return {
    BrowserWindow,
    app: {
      getAppPath: () => "/test/project/dashboard",
      getPath: () => "/test/profile",
      requestSingleInstanceLock: () => true,
      whenReady: () => Promise.resolve(),
      on() {}, once() {}, quit() {}, isPackaged: true,
    },
    safeStorage: { isEncryptionAvailable: () => true },
    ipcMain: { handle: (name: string, handler: (...args: any[]) => any) => h.handlers.set(name, handler) },
    session: { defaultSession: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, flushStorageData() {} } },
    shell: {},
  };
});

vi.mock("./connection", () => ({
  createConnectionStore: () => ({
    get: async () => ({ configured: h.configured, livekitUrl: h.configured ? "wss://saved.example" : "", hasGoogleApiKey: h.configured, hasLivekitApiKey: h.configured, hasLivekitApiSecret: h.configured }),
    childEnv: async () => h.configured && !h.environmentUnavailable ? { ...h.credentials } : Object.fromEntries(Object.keys(h.credentials).map((key) => [key, ""])),
    save: async (input: ConnectionInput) => {
      h.saveCalls++;
      if (h.rejected) return { error: "Credential validation failed.", field: "googleApiKey" };
      h.configured = true;
      h.credentials.GOOGLE_API_KEY = input.googleApiKey;
      return { settings: { configured: true, livekitUrl: "wss://saved.example", hasGoogleApiKey: true, hasLivekitApiKey: true, hasLivekitApiSecret: true } };
    },
  }),
}));

vi.mock("./ports", () => ({ findFreePort: async () => 12345 }));
vi.mock("./system", () => ({ SystemStatsPoller: class { start() {} stop() {} } }));
vi.mock("./supervisor", () => ({
  ManagedProcess: class {
    started = false;
    constructor(readonly name: string, _command: string, readonly args: string[], readonly options: any) { h.processes.push(this); }
    start() { this.started = true; h.lifecycle.push(`start:${this.name}`); }
    stop() { this.started = false; h.lifecycle.push(`stop:${this.name}`); }
    status() { return { name: this.name, state: this.started ? "starting" : "stopped", restarts: 0 }; }
  },
}));

const input = { googleApiKey: "replacement-google-key", livekitUrl: "wss://saved.example", livekitApiKey: "saved-livekit-key", livekitApiSecret: "saved-livekit-secret" };
const event = () => ({ sender: h.windows[0].webContents });

beforeEach(async () => {
  vi.resetModules();
  vi.stubEnv("IRA_EXTERNAL_AGENT", "0");
  h.handlers.clear(); h.windows.length = 0; h.processes.length = 0; h.lifecycle.length = 0;
  h.configured = false; h.environmentUnavailable = false; h.rejected = false; h.saveCalls = 0;
  h.credentials.GOOGLE_API_KEY = "saved-google-key";
  await import("./main");
  await vi.waitFor(() => expect(h.windows).toHaveLength(1));
});

describe("desktop credential integration", () => {
  it("starts the bridge with empty credentials and keeps a new user offline", async () => {
    expect(h.processes[0].options.env).toMatchObject({ GOOGLE_API_KEY: "", LIVEKIT_URL: "", LIVEKIT_API_KEY: "", LIVEKIT_API_SECRET: "" });
    expect(h.processes[0].args).toContain("--no-env-file");
    expect(h.processes.filter((proc) => proc.name === "agent" && proc.started)).toHaveLength(0);
    expect(await h.handlers.get("ira:agent:status")!()).toMatchObject({ configurationRequired: true });
  });

  it("saves credentials then starts both processes with the saved keys and notifies the session", async () => {
    const result = await h.handlers.get("ira:connection:save")!(event(), input);
    expect(result).toHaveProperty("settings.configured", true);
    for (const name of ["bridge", "agent"]) {
      const proc = h.processes.filter((item) => item.name === name).at(-1);
      expect(proc.started).toBe(true);
      expect(proc.options.env).toMatchObject(h.credentials);
    }
    expect(h.windows[0].webContents.send).toHaveBeenCalledWith("ira:connection:changed", undefined);
    expect(await h.handlers.get("ira:agent:status")!()).toMatchObject({ configurationRequired: false });

    h.lifecycle.length = 0;
    await h.handlers.get("ira:connection:save")!(event(), { ...input, googleApiKey: "second-google-key" });
    expect(h.lifecycle.slice(0, 2).sort()).toEqual(["stop:agent", "stop:bridge"]);
    expect(h.processes.filter((proc) => proc.started)).toHaveLength(2);
    expect(h.processes.at(-1).options.env.GOOGLE_API_KEY).toBe("second-google-key");
  });

  it("leaves running processes unchanged when validation fails", async () => {
    h.rejected = true;
    const previous = [...h.processes];
    const result = await h.handlers.get("ira:connection:save")!(event(), input);
    expect(result).toHaveProperty("error");
    expect(h.processes).toEqual(previous);
    expect(h.windows[0].webContents.send).not.toHaveBeenCalledWith("ira:connection:changed", undefined);
  });

  it("keeps voice offline if saved metadata exists but credentials cannot be unlocked", async () => {
    h.environmentUnavailable = true;
    await h.handlers.get("ira:connection:save")!(event(), input);
    expect(await h.handlers.get("ira:agent:status")!()).toMatchObject({ configurationRequired: true });
    expect(h.processes.filter((proc) => proc.name === "agent" && proc.started)).toHaveLength(0);
  });

  it("refuses credential operations from another window", async () => {
    const unknown = { sender: {} };
    await expect(Promise.resolve().then(() => h.handlers.get("ira:connection:get")!(unknown))).rejects.toThrow("Unknown window");
    await expect(Promise.resolve().then(() => h.handlers.get("ira:connection:save")!(unknown, input))).rejects.toThrow("Unknown window");
    expect(h.saveCalls).toBe(0);
  });
});
