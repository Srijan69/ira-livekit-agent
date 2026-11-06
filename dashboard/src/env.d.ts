/// <reference types="vite/client" />

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
} from "@shared/types";

/**
 * The preload bridge, as the renderer sees it.
 *
 * Optional because the renderer also runs under Vitest in jsdom and in the
 * browser-only dev server, where no preload exists. Code reaching for
 * `window.ira` must handle its absence -- which is also what makes the
 * components testable without Electron.
 *
 * Note what is not here: the bridge's port and shared secret. Main proxies
 * every bridge call, so this surface cannot mint a room token by itself.
 */
declare global {
  interface Window {
    ira?: {
      connection?: ConnectionApi;
      personalMemory?: PersonalMemoryApi;
      restart(): Promise<void>;
      tasks: TasksApi;
      skills?: SkillsApi;
      onStats(listener: (stats: SystemStats) => void): () => void;
      getStats(): Promise<SystemStats | null>;
      window: {
        minimize(): Promise<void>;
        close(): Promise<void>;
        toggleAlwaysOnTop(): Promise<boolean>;
      };
      session: {
        token(room?: string): Promise<TokenResult>;
        health(): Promise<HealthResult>;
      };
      agent: {
        status(): Promise<ProcessSnapshot>;
        onStatus(listener: (status: ProcessStatus) => void): () => void;
        logs(): Promise<{ agent: string[]; bridge: string[] }>;
        restart(): Promise<void>;
      };
    };
  }
}

export {};
