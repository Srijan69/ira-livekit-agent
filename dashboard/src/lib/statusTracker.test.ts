import { describe, expect, it } from "vitest";

import { activeCuesFrom, ERROR_FLASH_MS, orbStateFrom } from "./orbState";
import { EMPTY_TRACKER, orbStatusFrom, trackStatus } from "./statusTracker";

const at = (ms: number, state: string, detail?: string) => ({ state, detail, at: ms });

describe("trackStatus", () => {
  it("records the tool that started, by name", () => {
    const tracker = trackStatus(EMPTY_TRACKER, at(100, "DELEGATING", "remember"));
    expect(tracker.tool).toEqual({ name: "remember", lastUpdate: 100 });
  });

  it("keeps the starting name through progress lines", () => {
    // "step 1/3" is detail about the same call, not a different tool.
    let tracker = trackStatus(EMPTY_TRACKER, at(100, "DELEGATING", "run_workflow"));
    tracker = trackStatus(tracker, at(200, "RUNNING", "step 1/3"));
    expect(tracker.tool).toEqual({ name: "run_workflow", lastUpdate: 200 });
  });

  it("does not let lifecycle chatter end a tool call", () => {
    // The bug this guards: SPEAKING and LISTENING overwrote the last status,
    // so EXECUTE switched off the moment IRA narrated a running tool.
    let tracker = trackStatus(EMPTY_TRACKER, at(100, "DELEGATING", "open_app"));
    for (const state of ["SPEAKING", "LISTENING", "GENERATING", "RECOGNIZING"]) {
      tracker = trackStatus(tracker, at(150, state));
    }
    expect(tracker.tool?.name).toBe("open_app");
  });

  it("clears the tool on DONE", () => {
    let tracker = trackStatus(EMPTY_TRACKER, at(100, "DELEGATING", "open_app"));
    tracker = trackStatus(tracker, at(200, "DONE", "open_app"));
    expect(tracker.tool).toBeNull();
  });

  it("clears the tool and records the time on ERROR", () => {
    let tracker = trackStatus(EMPTY_TRACKER, at(100, "DELEGATING", "read_page"));
    tracker = trackStatus(tracker, at(250, "ERROR", "read_page"));
    expect(tracker.tool).toBeNull();
    expect(tracker.errorAt).toBe(250);
  });

  it("is case-insensitive about state names", () => {
    const tracker = trackStatus(EMPTY_TRACKER, at(100, "delegating", "remember"));
    expect(tracker.tool?.name).toBe("remember");
  });
});

describe("orbStatusFrom", () => {
  it("reports a fresh error", () => {
    const tracker = trackStatus(EMPTY_TRACKER, at(1_000, "ERROR"));
    expect(orbStatusFrom(tracker, 1_000 + ERROR_FLASH_MS - 1)?.state).toBe("ERROR");
  });

  it("forgets the error once the flash is over", () => {
    const tracker = trackStatus(EMPTY_TRACKER, at(1_000, "ERROR"));
    expect(orbStatusFrom(tracker, 1_000 + ERROR_FLASH_MS + 1)).toBeNull();
  });

  it("reports an in-flight tool as RUNNING with its name", () => {
    const tracker = trackStatus(EMPTY_TRACKER, at(1_000, "DELEGATING", "remember"));
    expect(orbStatusFrom(tracker, 1_100)).toEqual({
      state: "RUNNING",
      detail: "remember",
      at: 1_000,
    });
  });

  it("is null when nothing is happening", () => {
    expect(orbStatusFrom(EMPTY_TRACKER, 5_000)).toBeNull();
  });
});

describe("tracker + orb, end to end", () => {
  it("lights EXECUTE, LEARN and RESPOND while she narrates a memory write", () => {
    // The real sequence: tool starts, then she talks while it runs.
    let tracker = trackStatus(EMPTY_TRACKER, at(1_000, "DELEGATING", "remember"));
    tracker = trackStatus(tracker, at(1_050, "SPEAKING"));

    const input = {
      connection: "connected" as const,
      agentPresent: true,
      agentState: "speaking" as const,
      userSpeaking: false,
      lastStatus: orbStatusFrom(tracker, 1_100),
      now: 1_100,
    };

    expect(orbStateFrom(input)).toBe("speaking");
    expect(activeCuesFrom(input)).toEqual(
      expect.arrayContaining(["EXECUTE", "LEARN", "RESPOND"]),
    );
  });
});
