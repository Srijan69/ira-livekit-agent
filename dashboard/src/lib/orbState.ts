/**
 * The orb's state machine.
 *
 * A pure function of facts we can actually observe about the session, with no
 * React and no SVG in sight. That separation is deliberate: it makes the
 * animation's *logic* testable, leaving the component responsible only for
 * turning a state name into motion.
 */

export type OrbState =
  | "offline"
  | "connecting"
  | "listening"
  | "recognizing"
  | "generating"
  | "speaking"
  | "executing"
  | "error";

/** Values LiveKit publishes on the `lk.agent.state` participant attribute. */
export type AgentLifecycle =
  | "initializing"
  | "idle"
  | "listening"
  | "thinking"
  | "speaking";

/** One decoded message from the `ira.status` data topic. */
export type StatusEvent = {
  /** `SessionStatusReporter` state, e.g. DELEGATING / RUNNING / ERROR. */
  state: string;
  /** Free-text detail; carries the tool name during a tool call. */
  detail?: string;
  /** Milliseconds on the same clock as `OrbInput.now`. */
  at: number;
};

export type OrbInput = {
  connection: "disconnected" | "connecting" | "connected";
  /** Is IRA actually in the room? Separate from our own connection. */
  agentPresent: boolean;
  agentState?: AgentLifecycle;
  userSpeaking: boolean;
  lastStatus?: StatusEvent | null;
  /** Injected so the function stays pure and testable. */
  now?: number;
};

/** How long an amber error flash lasts before the orb settles back. */
export const ERROR_FLASH_MS = 1_500;

/**
 * Safety net for a dropped end-event, not a timeout.
 *
 * In-flight is normally decided by which status arrived last -- a RUNNING with
 * no DONE after it. This window only covers the case where the end-event never
 * arrives, so a crashed tool can't pin the orb on "executing" forever. It is
 * generous because a real page load can legitimately take fifteen seconds.
 */
export const TOOL_STALE_MS = 30_000;

/** States that mean a tool call is underway. */
const TOOL_IN_FLIGHT = new Set(["DELEGATING", "RUNNING", "VERIFYING"]);

/**
 * Tools that write to IRA's memory, which is what lights the LEARN cue.
 *
 * Writers only. `recall` reading a fact is not learning one, and a cue that
 * lights on every lookup stops meaning anything.
 */
const MEMORY_WRITE_TOOLS = [
  "remember",
  "save_note",
  "remember_preference",
  "save_workflow",
  "adapt_task",
  "add_task",
];

const isFresh = (event: StatusEvent, now: number, within: number): boolean =>
  now - event.at < within;

const toolInFlight = (input: OrbInput, now: number): boolean => {
  const status = input.lastStatus;
  if (!status) return false;
  if (!TOOL_IN_FLIGHT.has(status.state.toUpperCase())) return false;
  return isFresh(status, now, TOOL_STALE_MS);
};

const erroring = (input: OrbInput, now: number): boolean => {
  const status = input.lastStatus;
  if (!status) return false;
  if (status.state.toUpperCase() !== "ERROR") return false;
  return isFresh(status, now, ERROR_FLASH_MS);
};

/**
 * Resolve the single state the orb should render.
 *
 * Order is the whole design here. Audio output outranks background work
 * because the user can hear it; connection facts outrank everything because
 * showing "listening" when nobody is listening is a lie.
 */
export function orbStateFrom(input: OrbInput): OrbState {
  const now = input.now ?? Date.now();

  if (input.connection === "disconnected") return "offline";
  if (input.connection === "connecting" || !input.agentPresent) return "connecting";

  if (erroring(input, now)) return "error";

  if (input.agentState === "speaking") return "speaking";
  if (input.agentState === "thinking") return "generating";
  if (input.userSpeaking) return "recognizing";
  if (toolInFlight(input, now)) return "executing";

  return "listening";
}

/**
 * The caption under the orb.
 *
 * `error` returns an empty string on purpose: an amber flash is a flash, and
 * swapping the caption to ERROR for a second and a half reads as a crash. The
 * component keeps whatever it was showing.
 */
export function orbLabel(state: OrbState): string {
  switch (state) {
    case "offline":
      return "OFFLINE";
    case "connecting":
      return "CONNECTING...";
    case "listening":
    case "recognizing":
      return "LISTENING...";
    case "generating":
      return "THINKING...";
    case "speaking":
      return "SPEAKING...";
    case "executing":
      return "WORKING...";
    case "error":
      return "";
  }
}

export type Cue =
  | "THINK"
  | "ANALYZE"
  | "RESPOND"
  | "UNDERSTAND"
  | "LEARN"
  | "EXECUTE";

export const LEFT_CUES: readonly Cue[] = ["THINK", "ANALYZE", "RESPOND"];
export const RIGHT_CUES: readonly Cue[] = ["UNDERSTAND", "LEARN", "EXECUTE"];

/**
 * Which cues light up.
 *
 * Computed separately from the orb state, and able to return several at once,
 * because a tool call during narration is genuinely two things happening. In
 * the reference image these six words are static chrome; here they report.
 */
export function activeCuesFrom(input: OrbInput): Cue[] {
  const now = input.now ?? Date.now();
  if (input.connection !== "connected" || !input.agentPresent) return [];

  const cues = new Set<Cue>();

  if (input.userSpeaking) {
    cues.add("ANALYZE");
    cues.add("UNDERSTAND");
  }
  if (input.agentState === "thinking") cues.add("THINK");
  if (input.agentState === "speaking") cues.add("RESPOND");

  if (toolInFlight(input, now)) {
    cues.add("EXECUTE");
    const detail = (input.lastStatus?.detail ?? "").toLowerCase();
    if (MEMORY_WRITE_TOOLS.some((tool) => detail.includes(tool))) cues.add("LEARN");
  }

  return [...cues];
}
