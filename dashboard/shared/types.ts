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

/** Lifecycle of a supervised Python child process. */
export type ProcessState =
  | "starting"
  /** Spawned, but not yet confirmed able to do its job. */
  | "running"
  /** Printed its ready line -- for the agent, registered with LiveKit. */
  | "ready"
  | "restarting"
  | "failed"
  | "stopped";

export type ProcessStatus = {
  name: string;
  state: ProcessState;
  /** Populated when state is "failed", and shown to the user verbatim. */
  reason?: string;
  restarts: number;
};

/**
 * A minted room token, or why there isn't one.
 *
 * A result type rather than a thrown error: "the bridge hasn't finished
 * starting" is the normal case during launch, not an exception.
 */
export type TokenResult =
  | { url: string; room: string; token: string }
  | { error: string };

/** Configuration preflight. Booleans only -- never the values themselves. */
export type HealthResult = {
  bridge: string;
  config?: Record<string, boolean>;
  error?: string;
};

/** Both supervised processes, as the session controller sees them. */
export type ProcessSnapshot = {
  /**
   * False when IRA runs outside the dashboard (IRA_EXTERNAL_AGENT=1), e.g. in
   * a terminal so her logs stay where the boss can read them. The dashboard
   * then connects without waiting for a registration it will never observe.
   */
  supervised: boolean;
  agent: ProcessStatus | null;
  bridge: ProcessStatus | null;
};
