import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IraOrb } from "./IraOrb";

describe("IraOrb", () => {
  it("rotates HTML layers, never SVG groups", () => {
    // Motion puts transformed SVG elements in `transform-box: fill-box`, which
    // makes a pixel transform-origin relative to each group's own bounding
    // box. The rings rotated about points up to 82px off the orb's centre,
    // swung through the core, and clipped against the SVG's edge -- visible
    // in screenshots for two review passes before anyone measured it.
    //
    // HTML layers rotate about their own centre, which is the orb's, and
    // composite on the GPU instead of re-rasterising the SVG every frame.
    const { container } = render(<IraOrb state="listening" animate={false} />);

    const transformedSvgParts = Array.from(
      container.querySelectorAll<SVGElement>("svg *"),
    ).filter((el) => el.style.transform !== "");
    expect(transformedSvgParts).toEqual([]);

    expect(container.querySelectorAll("[data-orb-layer]")).toHaveLength(3);
  });

  it("captions nothing itself -- the stage owns the label", () => {
    // The caption cross-fades between states and holds through the error
    // flash; that logic lives in Stage, and a second copy here would drift.
    const { container } = render(<IraOrb state="speaking" animate={false} />);
    expect(container.textContent).toBe("IRA");
  });
});
