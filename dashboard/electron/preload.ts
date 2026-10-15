import { contextBridge, ipcRenderer } from "electron";

import type {
  HealthResult,
  ProcessSnapshot,
  ProcessStatus,
  SystemStats,
  TokenResult,
} from "../shared/types";

// The complete surface the renderer may touch. Nothing here hands out a
// module -- each entry is one named operation, so the renderer can never
// reach `fs`, `child_process`, or `require`.
//
// Note what is absent: the bridge's port and shared secret. Main proxies every
// bridge call, so a compromised renderer cannot mint a room token on its own.
const api = {
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
