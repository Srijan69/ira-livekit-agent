import { contextBridge, ipcRenderer } from "electron";

import type { SystemStats } from "./system";

// The complete surface the renderer may touch. Nothing here hands out a
// module -- each entry is one named operation, so the renderer can never
// reach `fs`, `child_process`, or `require`.
//
// Sub-project A exposes stats and window controls only. `agent`, `bridgePort`
// and `settings` from the design land with the bridge in B/C. They are absent
// rather than stubbed: a method that exists and returns null leaves the UI
// unable to tell "not built yet" from "broken".
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
};

export type IraApi = typeof api;

contextBridge.exposeInMainWorld("ira", api);
