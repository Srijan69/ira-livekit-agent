import { ERROR_FLASH_MS, type StatusEvent } from "./orbState";

/**
 * What the `ira.status` stream means for the orb, accumulated.
 *
 * The stream interleaves two kinds of event: tool lifecycle (DELEGATING,
 * RUNNING, VERIFYING, DONE, ERROR) and conversational lifecycle (SPEAKING,
 * LISTENING, GENERATING, RECOGNIZING). Keeping only the *latest* event let
 * the second kind erase the first -- EXECUTE went dark the moment IRA started
 * narrating a tool that was still running. So tools are tracked on their own,
 * and conversational states are ignored here: the orb already gets those,
 * more authoritatively, from the `lk.agent.state` attribute.
 */
export type StatusTracker = {
  /** The tool in flight, named by the call that started it. */
  tool: { name: string; lastUpdate: number } | null;
  /** When the most recent ERROR arrived, for the amber flash. */
  errorAt: number | null;
};

export const EMPTY_TRACKER: StatusTracker = { tool: null, errorAt: null };

const IN_FLIGHT = new Set(["DELEGATING", "RUNNING", "VERIFYING"]);

export function trackStatus(tracker: StatusTracker, event: StatusEvent): StatusTracker {
  const state = event.state.toUpperCase();

  if (IN_FLIGHT.has(state)) {
    return {
      ...tracker,
      tool: {
        // The first name wins until the call ends. Tools emit their own
        // progress ("desktop worker", "step 1/3") through the same reporter,
        // and those describe the running call rather than a new one.
        name: tracker.tool?.name ?? event.detail ?? "",
        lastUpdate: event.at,
      },
    };
  }

  if (state === "DONE") return { ...tracker, tool: null };
  if (state === "ERROR") return { tool: null, errorAt: event.at };

  return tracker;
}

/**
 * The single status the orb's state machine should see right now.
 *
 * A fresh error outranks a running tool -- the flash is brief and means
 * something just went wrong -- and both are derived from `now`, which is why
 * the caller must re-render when the flash window closes.
 */
export function orbStatusFrom(tracker: StatusTracker, now: number): StatusEvent | null {
  if (tracker.errorAt !== null && now - tracker.errorAt < ERROR_FLASH_MS) {
    return { state: "ERROR", at: tracker.errorAt };
  }
  if (tracker.tool) {
    return { state: "RUNNING", detail: tracker.tool.name, at: tracker.tool.lastUpdate };
  }
  return null;
}
