import { contextBridge, ipcRenderer } from "electron";

import type {
  HealthResult,
  ProcessSnapshot,
  ProcessStatus,
  SystemStats,
  TokenResult,
  TasksApi,
  SkillsApi,
  ConnectionApi,
  PersonalMemoryApi,
} from "../shared/types";

// The complete surface the renderer may touch. Nothing here hands out a
// module -- each entry is one named operation, so the renderer can never
// reach `fs`, `child_process`, or `require`.
//
// Note what is absent: the bridge's port and shared secret. Main proxies every
// bridge call, so a compromised renderer cannot mint a room token on its own.
const api = {
  personalMemory: {
    profile: () => ipcRenderer.invoke("ira:personal-memory:profile"),
    saveProfile: (input) => ipcRenderer.invoke("ira:personal-memory:saveProfile", input),
    resetProfile: () => ipcRenderer.invoke("ira:personal-memory:resetProfile"),
    settings: () => ipcRenderer.invoke("ira:personal-memory:settings"),
    saveSettings: (input) => ipcRenderer.invoke("ira:personal-memory:saveSettings", input),
    list: (query, limit) => ipcRenderer.invoke("ira:personal-memory:list", query, limit),
    status: () => ipcRenderer.invoke("ira:personal-memory:status"),
    deleteMemory: (id) => ipcRenderer.invoke("ira:personal-memory:deleteMemory", id),
    clearMemory: () => ipcRenderer.invoke("ira:personal-memory:clearMemory"),
    onChanged: (listener) => {
      const handler = () => listener();
      ipcRenderer.on("ira:personal-memory:changed", handler);
      return () => ipcRenderer.removeListener("ira:personal-memory:changed", handler);
    },
  } satisfies PersonalMemoryApi,
  connection: {
    get: () => ipcRenderer.invoke("ira:connection:get"),
    save: (input) => ipcRenderer.invoke("ira:connection:save", input),
    onChanged: (listener) => {
      const handler = () => listener();
      ipcRenderer.on("ira:connection:changed", handler);
      return () => ipcRenderer.removeListener("ira:connection:changed", handler);
    },
  } satisfies ConnectionApi,
  /** Persist preferences, shut down IRA, and relaunch the desktop app. */
  restart(): Promise<void> {
    return ipcRenderer.invoke("ira:app:restart");
  },

  /** Subscribe to host stats. Returns an unsubscribe function. */
  onStats(listener: (stats: SystemStats) => void): () => void {
    const handler = (_event: unknown, stats: SystemStats) => listener(stats);
    ipcRenderer.on("ira:stats", handler);
    return () => ipcRenderer.removeListener("ira:stats", handler);
  },

  /** Last known snapshot, so a fresh mount paints without waiting a tick. */
  getStats(): Promise<SystemStats | null> {
    return ipcRenderer.invoke("ira:stats:snapshot");
  },

  window: {
    minimize(): Promise<void> {
      return ipcRenderer.invoke("ira:window:minimize");
    },
    close(): Promise<void> {
      return ipcRenderer.invoke("ira:window:close");
    },
    /** Resolves to the new always-on-top state. */
    toggleAlwaysOnTop(): Promise<boolean> {
      return ipcRenderer.invoke("ira:window:toggle-always-on-top");
    },
  },

  session: {
    /** Mint a room token. The dispatch for IRA is baked into it. */
    token(room?: string): Promise<TokenResult> {
      return ipcRenderer.invoke("ira:session:token", room);
    },
    /** Configuration preflight -- answers "why is nothing connecting". */
    health(): Promise<HealthResult> {
      return ipcRenderer.invoke("ira:health");
    },
  },

  tasks: {
    list: (limit) => ipcRenderer.invoke("ira:tasks:list", limit),
    create: (input) => ipcRenderer.invoke("ira:tasks:create", input),
    update: (id, patch) => ipcRenderer.invoke("ira:tasks:update", id, patch),
    delete: (id) => ipcRenderer.invoke("ira:tasks:delete", id),
  } satisfies TasksApi,

  skills: {
    list: (archived) => ipcRenderer.invoke("ira:skills:list", archived),
    detail: (slug, version) => ipcRenderer.invoke("ira:skills:detail", slug, version),
    history: (limit) => ipcRenderer.invoke("ira:skills:history", limit),
    suggestions: () => ipcRenderer.invoke("ira:skills:suggestions"),
    pin: (slug, pinned) => ipcRenderer.invoke("ira:skills:pin", slug, pinned),
    archive: (slug) => ipcRenderer.invoke("ira:skills:archive", slug),
    restore: (slug) => ipcRenderer.invoke("ira:skills:restore", slug),
  } satisfies SkillsApi,

  agent: {
    status(): Promise<ProcessSnapshot> {
      return ipcRenderer.invoke("ira:agent:status");
    },
    onStatus(listener: (status: ProcessStatus) => void): () => void {
      const handler = (_event: unknown, status: ProcessStatus) => listener(status);
      ipcRenderer.on("ira:agent:status-push", handler);
      return () => ipcRenderer.removeListener("ira:agent:status-push", handler);
    },
    logs(): Promise<{ agent: string[]; bridge: string[] }> {
      return ipcRenderer.invoke("ira:agent:logs");
    },
    restart(): Promise<void> {
      return ipcRenderer.invoke("ira:agent:restart");
    },
  },
};

export type IraApi = typeof api;

contextBridge.exposeInMainWorld("ira", api);
