import { describe, expect, it } from "vitest";

import {
  ERROR_FLASH_MS,
  activeCuesFrom,
  orbLabel,
  orbStateFrom,
  type OrbInput,
} from "./orbState";

/**
 * The orb is a pure function of observable session facts, tested without
 * rendering anything. Keeping the machine separate from the SVG is the only
 * reason "smooth animation" is testable at all -- the alternative is asserting
 * against transform matrices.
 */

const base: OrbInput = {
  connection: "connected",
  agentPresent: true,
  agentState: "listening",
  userSpeaking: false,
  lastStatus: null,
  now: 10_000,
};

describe("orbStateFrom", () => {
  it("is offline when the room is disconnected", () => {
    expect(orbStateFrom({ ...base, connection: "disconnected" })).toBe("offline");
  });

  it("is connecting while the room is still joining", () => {
    expect(orbStateFrom({ ...base, connection: "connecting" })).toBe("connecting");
  });

  it("is connecting when we are in the room but IRA has not joined yet", () => {
    // Explicit dispatch means our join and hers are separate events. Showing
    // "listening" in the gap would claim she can hear us when she isn't there.
    expect(orbStateFrom({ ...base, agentPresent: false })).toBe("connecting");
  });

  it("is listening once she is present and idle", () => {
    expect(orbStateFrom(base)).toBe("listening");
  });

  it("is recognizing while the user is speaking", () => {
    expect(orbStateFrom({ ...base, userSpeaking: true })).toBe("recognizing");
  });

  it("is generating while she is thinking", () => {
    expect(orbStateFrom({ ...base, agentState: "thinking" })).toBe("generating");
  });

  it("is speaking while she is speaking", () => {
    expect(orbStateFrom({ ...base, agentState: "speaking" })).toBe("speaking");
  });

  it("is executing while a tool call is in flight", () => {
    expect(
      orbStateFrom({
        ...base,
        agentState: "idle",
        lastStatus: { state: "RUNNING", detail: "step 1/3", at: 9_900 },
      }),
    ).toBe("executing");
  });

  describe("precedence", () => {
    it("prefers speaking over an in-flight tool call", () => {
      // The user can hear her. The waveform has to track real audio, so audio
      // output outranks a background tool. The EXECUTE cue still lights --
      // that is what activeCuesFrom is for.
      expect(
        orbStateFrom({
          ...base,
          agentState: "speaking",
          lastStatus: { state: "RUNNING", at: 9_900 },
        }),
      ).toBe("speaking");
    });

    it("prefers offline over every session detail", () => {
      expect(
        orbStateFrom({
          ...base,
          connection: "disconnected",
          agentState: "speaking",
          userSpeaking: true,
          lastStatus: { state: "RUNNING", at: 9_900 },
        }),
      ).toBe("offline");
    });

    it("prefers a fresh error over speaking", () => {
      expect(
        orbStateFrom({
          ...base,
          agentState: "speaking",
          lastStatus: { state: "ERROR", detail: "tool call", at: 9_900 },
        }),
      ).toBe("error");
    });
  });

  describe("the error flash is transient", () => {
    it("shows error inside the flash window", () => {
      expect(
        orbStateFrom({
          ...base,
          lastStatus: { state: "ERROR", at: 10_000 - (ERROR_FLASH_MS - 1) },
        }),
      ).toBe("error");
    });

    it("settles back to the underlying state once the window passes", () => {
      // A permanent error state would mean one failed tool call leaves the orb
      // red for the rest of the session.
      expect(
        orbStateFrom({
          ...base,
          lastStatus: { state: "ERROR", at: 10_000 - (ERROR_FLASH_MS + 1) },
        }),
      ).toBe("listening");
    });
  });

  it("ignores a stale tool status rather than sticking on executing", () => {
    expect(
      orbStateFrom({
        ...base,
        agentState: "listening",
        lastStatus: { state: "RUNNING", at: 10_000 - 60_000 },
      }),
    ).toBe("listening");
  });
});

describe("orbLabel", () => {
  it("labels every caption-bearing state, so the orb never goes blank", () => {
    // `error` is excluded on purpose -- see the next test. Every other state
    // must caption itself, or the orb shows an empty line.
    const states = [
      "offline",
      "connecting",
      "listening",
      "recognizing",
      "generating",
      "speaking",
      "executing",
    ] as const;
    for (const state of states) {
      expect(orbLabel(state)).not.toBe("");
    }
  });

  it("captions recognizing as LISTENING, because that is what she is doing", () => {
    expect(orbLabel("recognizing")).toBe("LISTENING...");
  });

  it("keeps the caption from the prior state during an error flash", () => {
    // An amber flash is a flash. Replacing the caption with ERROR for 1.5s
    // reads as a crash.
    expect(orbLabel("error")).toBe("");
  });
});

describe("activeCuesFrom", () => {
  it("lights nothing when offline", () => {
    expect(activeCuesFrom({ ...base, connection: "disconnected" })).toEqual([]);
  });

  it("lights ANALYZE and UNDERSTAND while the user speaks", () => {
    const cues = activeCuesFrom({ ...base, userSpeaking: true });
    expect(cues).toContain("ANALYZE");
    expect(cues).toContain("UNDERSTAND");
  });

  it("lights THINK while generating", () => {
    expect(activeCuesFrom({ ...base, agentState: "thinking" })).toContain("THINK");
  });

  it("lights RESPOND while speaking", () => {
    expect(activeCuesFrom({ ...base, agentState: "speaking" })).toContain("RESPOND");
  });

  it("lights EXECUTE during a tool call even while she narrates it", () => {
    const cues = activeCuesFrom({
      ...base,
      agentState: "speaking",
      lastStatus: { state: "RUNNING", detail: "open_app", at: 9_900 },
    });
    expect(cues).toContain("EXECUTE");
    expect(cues).toContain("RESPOND");
  });

  it("lights LEARN when the tool in flight writes to memory", () => {
    expect(
      activeCuesFrom({
        ...base,
        agentState: "idle",
        lastStatus: { state: "RUNNING", detail: "remember", at: 9_900 },
      }),
    ).toContain("LEARN");
  });

  it("does not light LEARN for a tool that only reads", () => {
    expect(
      activeCuesFrom({
        ...base,
        agentState: "idle",
        lastStatus: { state: "RUNNING", detail: "read_page", at: 9_900 },
      }),
    ).not.toContain("LEARN");
  });
});
