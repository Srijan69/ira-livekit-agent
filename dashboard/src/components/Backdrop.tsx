import { useMemo } from "react";

/**
 * The scene behind the dashboard: a night city seen through curved glass.
 *
 * Generated rather than a bitmap. The reference image has the whole UI baked
 * into its background, so it can't be used as one -- and a procedural scene
 * scales to any window size, costs nothing to ship, and retunes from the
 * theme tokens. Every layer is non-interactive and never re-renders.
 */

type Building = {
  x: number;
  width: number;
  height: number;
  /** Lit window rows, as fractions of the building height. */
  lights: number[];
};

/**
 * Deterministic pseudo-random, seeded.
 *
 * `Math.random()` here would redraw a different skyline on every React render,
 * which reads as a flicker rather than a city.
 */
function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function skyline(seed: number, count: number, maxHeight: number): Building[] {
  const rand = mulberry32(seed);
  const buildings: Building[] = [];
  let x = -2;

  while (x < 102 && buildings.length < count) {
    const width = 2 + rand() * 5;
    const height = maxHeight * (0.25 + rand() * 0.75);
    const rows = Math.max(1, Math.floor(height / 14));
    const lights: number[] = [];
    for (let r = 0; r < rows; r += 1) {
      // Sparse. A fully lit tower reads as a bar chart, not a building.
      if (rand() > 0.55) lights.push((r + 0.5) / rows);
    }
    buildings.push({ x, width, height, lights });
    x += width + rand() * 1.6;
  }

  return buildings;
}

export function Backdrop() {
  // Two ranges: a dim far bank and a nearer, taller one, which is what gives
  // the skyline depth without a second image.
  const far = useMemo(() => skyline(0x1a7f, 34, 150), []);
  const near = useMemo(() => skyline(0x5eed, 26, 230), []);

  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* Base: deep navy, lifting toward the horizon behind the orb. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 90% at 50% 86%, #123050 0%, #0a1a2e 38%, #050b16 78%)",
        }}
      />

      {/* City. Sits in the lower half, behind everything. */}
      <svg
        className="absolute inset-x-0 bottom-0 h-[62%] w-full"
        viewBox="0 0 100 260"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id="far-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1b4d7a" stopOpacity="0.7" />
            <stop offset="100%" stopColor="#123a5e" stopOpacity="0.16" />
          </linearGradient>
          <linearGradient id="near-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0b1d30" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#0b1d30" stopOpacity="0.55" />
          </linearGradient>
        </defs>

        {/* Horizon haze -- the city's own light pollution. */}
        <ellipse cx="50" cy="258" rx="74" ry="48" fill="#3f8fd0" opacity="0.26" />

        {far.map((b, i) => (
          <g key={`far-${i}`}>
            <rect
              x={b.x}
              y={260 - b.height}
              width={b.width}
              height={b.height}
              fill="url(#far-fade)"
            />
            {b.lights.map((row, j) => (
              <rect
                key={j}
                x={b.x + b.width * 0.3}
                y={260 - b.height * row}
                width={b.width * 0.4}
                height="1.1"
                fill="#8fd4f5"
                opacity="0.18"
              />
            ))}
          </g>
        ))}

        {near.map((b, i) => (
          <g key={`near-${i}`}>
            <rect
              x={b.x}
              y={260 - b.height}
              width={b.width}
              height={b.height}
              fill="url(#near-fade)"
            />
            {b.lights.map((row, j) => (
              <rect
                key={j}
                x={b.x + b.width * 0.22}
                y={260 - b.height * row}
                width={b.width * 0.52}
                height="1.4"
                fill="#ffd9a0"
                opacity={0.1 + (j % 3) * 0.07}
              />
            ))}
          </g>
        ))}
      </svg>

      {/* The glass we're looking through: curved mullions, faintly lit. */}
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 1440 960"
        preserveAspectRatio="none"
      >
        <g stroke="#5aa9e6" strokeWidth="1" fill="none" opacity="0.1">
          {/* Verticals bow outward from the centre, as a curved wall does. */}
          {[180, 400, 620, 820, 1040, 1260].map((x) => {
            const bow = (x - 720) * 0.045;
            return (
              <path key={x} d={`M ${x} 0 Q ${x + bow} 480 ${x} 960`} />
            );
          })}
          {/* Head and transom rails, arced. */}
          <path d="M 0 118 Q 720 72 1440 118" />
          <path d="M 0 742 Q 720 700 1440 742" />
        </g>
      </svg>

      {/* Cyan cast from the orb, then a vignette to seat the panels. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(42% 38% at 50% 44%, rgb(34 211 238 / 0.09) 0%, transparent 70%)",
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(130% 115% at 50% 50%, transparent 52%, rgb(2 6 14 / 0.72) 100%)",
        }}
      />
    </div>
  );
}
