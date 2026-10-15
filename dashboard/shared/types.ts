/**
 * Types crossing the main/renderer boundary.
 *
 * Imported by both builds so the IPC contract can't drift. Duplicating these
 * in the renderer would mean a field rename in main compiles cleanly and
 * fails at runtime -- the exact bug the boundary exists to prevent.
 */

/** One snapshot of the host, shaped for the four rings in System Status. */
export type SystemStats = {
  /** Percent 0-100. */
  cpu: number;
  /** Percent of physical memory in use, 0-100. */
  memory: number;
  /** Percent used of the largest mounted volume, 0-100. */
  storage: number;
  battery: {
    percent: number;
    charging: boolean;
    /** False on a desktop PC, so the UI can show a plug instead of 0%. */
    hasBattery: boolean;
  };
  /** ISO-8601 timestamp of the read, for staleness display. */
  at: string;
};
