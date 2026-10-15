import si from "systeminformation";

import type { SystemStats } from "../shared/types";

export type { SystemStats };

// Two cadences, not one. CPU and memory move second to second and are cheap to
// read; disk size and battery move over minutes and `fsSize()` is the most
// expensive call here (it shells out per mount). Polling everything at the fast
// rate would spend most of the budget on numbers that hadn't changed.
const FAST_INTERVAL_MS = 2_000;
const SLOW_INTERVAL_MS = 10_000;

const clampPercent = (value: unknown): number => {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.max(0, Math.min(100, Math.round(n)));
};

/**
 * Polls the host and hands snapshots to `onStats`.
 *
 * Every read is wrapped: a dashboard that goes blank because one WMI query
 * failed is worse than one showing a slightly stale number, so a failed read
 * keeps the previous value instead of propagating.
 */
export class SystemStatsPoller {
  private fastTimer: NodeJS.Timeout | null = null;
  private slowTimer: NodeJS.Timeout | null = null;
  private slow = {
    storage: 0,
    battery: { percent: 0, charging: false, hasBattery: false },
  };
  private last: SystemStats | null = null;

  constructor(private readonly onStats: (stats: SystemStats) => void) {}

  get running(): boolean {
    return this.fastTimer !== null;
  }

  start(): void {
    if (this.running) return;
    void this.readSlow().then(() => this.readFast());
    this.fastTimer = setInterval(() => void this.readFast(), FAST_INTERVAL_MS);
    this.slowTimer = setInterval(() => void this.readSlow(), SLOW_INTERVAL_MS);
  }

  /**
   * Stopped while the window is hidden or blurred. An invisible dashboard
   * polling the disk every two seconds is the reason these apps get a
   * reputation for eating battery.
   */
  stop(): void {
    if (this.fastTimer) clearInterval(this.fastTimer);
    if (this.slowTimer) clearInterval(this.slowTimer);
    this.fastTimer = null;
    this.slowTimer = null;
  }

  /** Last snapshot, so a newly-shown window paints immediately. */
  snapshot(): SystemStats | null {
    return this.last;
  }

  private async readFast(): Promise<void> {
    try {
      const [load, mem] = await Promise.all([si.currentLoad(), si.mem()]);
      const used = mem.total > 0 ? ((mem.total - mem.available) / mem.total) * 100 : 0;
      this.last = {
        cpu: clampPercent(load.currentLoad),
        memory: clampPercent(used),
        storage: this.slow.storage,
        battery: this.slow.battery,
        at: new Date().toISOString(),
      };
      this.onStats(this.last);
    } catch {
      // Keep the previous snapshot rather than reporting zeros.
    }
  }

  private async readSlow(): Promise<void> {
    try {
      const disks = await si.fsSize();
      // The biggest mounted volume, not disks[0]: on Windows that can be a
      // recovery partition sitting at 97% full, which would show a permanent
      // storage warning for a machine with a half-empty C: drive.
      const primary = disks
        .filter((d) => typeof d.size === "number" && d.size > 0)
        .sort((a, b) => b.size - a.size)[0];
      if (primary) this.slow.storage = clampPercent(primary.use);
    } catch {
      /* keep previous */
    }

    try {
      const battery = await si.battery();
      this.slow.battery = {
        percent: clampPercent(battery.percent),
        charging: Boolean(battery.isCharging || battery.acConnected),
        // A desktop PC has no battery. The UI needs to know that so it can
        // show a plug instead of a ring reading zero percent, which looks
        // like a dying machine.
        hasBattery: Boolean(battery.hasBattery),
      };
    } catch {
      /* keep previous */
    }
  }
}
