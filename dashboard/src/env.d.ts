import type { SystemStats } from "@shared/types";

/**
 * The preload bridge, as the renderer sees it.
 *
 * Optional because the renderer also runs under Vitest in jsdom, where no
 * preload exists. Code reaching for `window.ira` must handle its absence --
 * which is also what makes the components testable without Electron.
 */
declare global {
  interface Window {
    ira?: {
      onStats(listener: (stats: SystemStats) => void): () => void;
      getStats(): Promise<SystemStats | null>;
      window: {
        minimize(): Promise<void>;
        close(): Promise<void>;
        toggleAlwaysOnTop(): Promise<boolean>;
      };
    };
  }
}

export {};
