import { describe, expect, it } from "vitest";
import { parseComputerObservation } from "@shared/computerUse";
import { EMPTY_COMPUTER_USE, computerUseStatus, trackComputerUse } from "./computerUse";

// Real 1x1 PNG. The parser must verify the image header, not trust its label.
const image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=";
const frame = (overrides: Record<string, unknown> = {}) => ({
  type: "computer.observation", run_id: "run", call_id: "call", tool_name: "desktop_click",
  sequence: 1, phase: "before", observation_id: "screen", captured_at: "2026-11-07T12:00:00.000Z",
  image_size: [1, 1], screenshot_data_url: image, ...overrides,
});
const at = Date.parse("2026-11-07T12:00:00.000Z");
const event = (overrides: Record<string, unknown> = {}) => parseComputerObservation(frame(overrides))!;

describe("computer observation boundary", () => {
  it("accepts a real image and strips typed text and exception payloads", () => {
    const parsed = event({ text: "secret", error: "private", action: {
      action: "type", observation_id: "screen", image_points: [[0, 0]], text: "password", result: "private",
    } });
    expect(parsed?.screenshot_data_url).toBe(image);
    expect(parsed?.action).toEqual({ action: "type", observation_id: "screen", image_points: [[0, 0]] });
    expect(JSON.stringify(parsed)).not.toMatch(/secret|password|private/);
  });
  it.each([
    { screenshot_data_url: "https://example.com/screen.png" },
    { screenshot_data_url: "data:image/svg+xml;base64,PHN2Zz4=" },
    { screenshot_data_url: "data:image/png;base64,YWJjZA==" },
    { image_size: [2, 1] }, { sequence: -1 }, { captured_at: "yesterday" },
    { action: { action: "click", observation_id: "other", image_points: [[0, 0]] } },
    { cursor_image_position: [NaN, 0] },
  ])("rejects malformed image or uncorrelated geometry %#", (patch) => {
    expect(parseComputerObservation(frame(patch))).toBeNull();
  });
  it("clips partially visible target bounds and drops offscreen targets", () => {
    const parsed = event({ targets: [
      { id: "visible", name: "Control", role: "button", image_bounds: [-1, 0, 2, 1], text: "private" },
      { id: "gone", name: "Gone", role: "button", image_bounds: [2, 0, 1, 1] },
    ] });
    expect(parsed.targets).toEqual([{ id: "visible", name: "Control", role: "button", image_bounds: [0, 0, 1, 1] }]);
  });
});

describe("computer use evidence", () => {
  it("keeps actual before and after frames and rejects late retransmissions", () => {
    const before = trackComputerUse(EMPTY_COMPUTER_USE, event(), at);
    const after = trackComputerUse(before, event({ sequence: 2, phase: "after", observation_id: "result" }), at + 1_000);
    expect(after.before?.event.observation_id).toBe("screen");
    expect(after.latest?.event.observation_id).toBe("result");
    expect(trackComputerUse(after, event(), at + 2_000)).toBe(after);
  });
  it("does not attach a previous call's screen to a new action", () => {
    const old = trackComputerUse(EMPTY_COMPUTER_USE, event(), at);
    const next = trackComputerUse(old, event({ sequence: 2, call_id: "new", observation_id: "new-screen" }), at + 1_000);
    expect(next.before?.event.call_id).toBe("new");
    expect(next.latest?.event.call_id).toBe("new");
  });
  it("rejects a retired run after a newer run starts", () => {
    const first = trackComputerUse(EMPTY_COMPUTER_USE, event(), at);
    const second = trackComputerUse(first, event({ run_id: "new" }), at + 1_000);
    expect(trackComputerUse(second, event({ sequence: 2 }), at + 2_000)).toBe(second);
  });
  it("marks capture age stale even when an old image just arrived", () => {
    const state = trackComputerUse(EMPTY_COMPUTER_USE, event(), at + 60_000);
    expect(computerUseStatus(state, true, at + 60_001)).toBe("stale");
    expect(computerUseStatus(state, false, at + 60_001)).toBe("disconnected");
    expect(computerUseStatus(EMPTY_COMPUTER_USE, true, at)).toBe("empty");
  });
  it("clears screenshots explicitly and bounds timeline memory", () => {
    let state = EMPTY_COMPUTER_USE;
    for (let n = 1; n <= 20; n++) state = trackComputerUse(state, event({ sequence: n, call_id: `call-${n}` }), at + n);
    expect(state.timeline.length).toBeLessThanOrEqual(6);
    const cleared = trackComputerUse(state, event({ sequence: 21, phase: "cleared", screenshot_data_url: undefined, image_size: undefined }), at + 30);
    expect(cleared.latest).toBeNull();
    expect(cleared.before).toBeNull();
    expect(cleared.timeline).toEqual([]);
  });
});
