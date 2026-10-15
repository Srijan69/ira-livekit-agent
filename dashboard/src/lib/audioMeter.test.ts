import { describe, expect, it } from "vitest";

import { levelFrom } from "./audioMeter";

describe("levelFrom", () => {
  it("is zero for silence", () => {
    expect(levelFrom(new Uint8Array(128))).toBe(0);
  });

  it("is zero for an empty buffer rather than NaN", () => {
    // NaN would reach the canvas as a bar height and draw nothing, silently.
    expect(levelFrom(new Uint8Array(0))).toBe(0);
  });

  it("rises with loudness", () => {
    const quiet = levelFrom(new Uint8Array(128).fill(20));
    const loud = levelFrom(new Uint8Array(128).fill(90));
    expect(loud).toBeGreaterThan(quiet);
  });

  it("never exceeds 1, however loud", () => {
    expect(levelFrom(new Uint8Array(128).fill(255))).toBe(1);
  });
});
