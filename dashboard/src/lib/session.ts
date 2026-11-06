import type { ConnectionState } from "@/components/ConnectionBadge";
import type { Cue, OrbState } from "@/lib/orbState";
import type { IraEvent } from "@shared/types";
import type { ExecutionActivity } from "./execution";

/**
 * The seam between the UI and IRA.
 *
 * Declared here rather than in either implementation so the two can't drift:
 * `useLiveKitSession` and `useMockSession` both satisfy this, and
 * `useSession` picks one at module load. Every component downstream is
 * written against this type rather than against LiveKit, which is what made
 * swapping the mock for the real thing a one-import change.
 */
export type Session = {
  activities: ExecutionActivity[];
  clearExecutionHistory: () => void;
  subscribeEvents: (listener: (event: IraEvent) => void) => () => void;
  connection: ConnectionState;
  orbState: OrbState;
  cues: Cue[];
  micEnabled: boolean;
  toggleMic: () => void;
  /** Current output loudness, 0..1. Called once per animation frame. */
  getLevel: () => number;
  /** Sends text to IRA, over the lk.chat topic when there's a room. */
  send: (text: string) => void;
  /** Set when we can say why there is no session. Null when fine. */
  error: string | null;
};
