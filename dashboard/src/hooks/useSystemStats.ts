import { useEffect, useState } from "react";

import type { SystemStats } from "@shared/types";

/**
 * Host stats from the Electron main process.
 *
 * Returns null when there is no preload bridge -- under Vitest, or if someone
 * opens the renderer in a plain browser. Components render a dash rather than
 * zeros in that case, because a CPU ring sitting confidently at 0% is a lie
 * and 87% battery on a machine with no battery is a worse one.
 */
export function useSystemStats(): SystemStats | null {
  const [stats, setStats] = useState<SystemStats | null>(null);

  useEffect(() => {
    const bridge = window.ira;
    if (!bridge) return;

    // Paint from the last snapshot immediately; the first live push is up to
    // two seconds away and empty rings in that window look like a failure.
    let cancelled = false;
    void bridge.getStats().then((snapshot) => {
      if (!cancelled && snapshot) setStats(snapshot);
    });

    const unsubscribe = bridge.onStats(setStats);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  return stats;
}
