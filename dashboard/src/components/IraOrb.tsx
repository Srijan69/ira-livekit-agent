import { motion } from "motion/react";

import { useSpinner } from "@/hooks/useSpinner";
import type { OrbState } from "@/lib/orbState";

/**
 * Per-state motion, in degrees per second.
 *
 * The listening row is Section 3's spec expressed as rates: a 60-second outer
 * revolution is 6 deg/s, a 40-second reverse is -9, a 24-second inner is 15.
 * `generating` is that times 2.5. Reading them as rates rather than durations
 * is what lets the speed be interpolated between states instead of restarted.
 */
const MOTION: Record<
  OrbState,
  { outer: number; mid: number; inner: number; intensity: number; breathe: number }
> = {
  offline: { outer: 0.6, mid: -0.9, inner: 1.5, intensity: 0.22, breathe: 1.004 },
  connecting: { outer: 4, mid: -6, inner: 10, intensity: 0.45, breathe: 1.02 },
  listening: { outer: 6, mid: -9, inner: 15, intensity: 0.75, breathe: 1.03 },
  recognizing: { outer: 9, mid: -13, inner: 20, intensity: 0.95, breathe: 1.02 },
  generating: { outer: 15, mid: -22.5, inner: 37.5, intensity: 1, breathe: 1.015 },
  speaking: { outer: 8, mid: -12, inner: 22, intensity: 1, breathe: 1.042 },
  executing: { outer: 11, mid: -16, inner: 26, intensity: 0.9, breathe: 1.025 },
  error: { outer: 6, mid: -9, inner: 15, intensity: 0.85, breathe: 1.03 },
};

const TINT: Record<string, { ring: string; core: string }> = {
  default: { ring: "#22D3EE", core: "#CFF8FF" },
  offline: { ring: "#64748B", core: "#B6C6D9" },
  error: { ring: "#F59E0B", core: "#FFE9BF" },
};

const tintFor = (state: OrbState) =>
  state === "offline" ? TINT.offline : state === "error" ? TINT.error : TINT.default;

/** Evenly spaced tick marks around the outer ring. */
function Ticks({ color, opacity }: { color: string; opacity: number }) {
  return (
    <g stroke={color} strokeWidth="1.5" opacity={opacity} strokeLinecap="round">
      {Array.from({ length: 60 }, (_, i) => {
        const angle = (i / 60) * Math.PI * 2;
        const long = i % 5 === 0;
        const r1 = long ? 176 : 181;
        const r2 = 186;
        return (
          <line
            key={i}
            x1={200 + Math.cos(angle) * r1}
            y1={200 + Math.sin(angle) * r1}
            x2={200 + Math.cos(angle) * r2}
            y2={200 + Math.sin(angle) * r2}
            opacity={long ? 1 : 0.45}
          />
        );
      })}
    </g>
  );
}

/**
 * The orb.
 *
 * Three independently counter-rotating ring groups plus a breathing core. All
 * animation is `transform` and `opacity`, so the whole thing composites on the
 * GPU; the breathing is a CSS keyframe with its amplitude in a custom property
 * because changing a Motion keyframe array would restart the cycle on every
 * state change.
 */
export function IraOrb({
  state,
  animate,
  size = 300,
}: {
  state: OrbState;
  /** False freezes everything -- reduced motion, or a hidden window. */
  animate: boolean;
  size?: number;
}) {
  const motionSpec = MOTION[state];
  const tint = tintFor(state);

  const outer = useSpinner(motionSpec.outer, animate);
  const mid = useSpinner(motionSpec.mid, animate);
  const inner = useSpinner(motionSpec.inner, animate);

  const { intensity } = motionSpec;

  return (
    <div
      className="relative grid place-items-center"
      style={{ width: size, height: size }}
    >
      {/* The dais the orb sits on, as in the reference: a lit ellipse with a
          rim, well below the rings so it reads as a surface rather than
          another ring. Outside the SVG because it has to sit under the halo. */}
      <div
        aria-hidden
        className="absolute left-1/2 -translate-x-1/2"
        style={{
          bottom: -size * 0.035,
          width: size * 0.82,
          height: size * 0.2,
          borderRadius: "50%",
          background: `radial-gradient(ellipse at center, ${tint.ring}3A 0%, ${tint.ring}12 45%, transparent 74%)`,
          borderTop: `1.5px solid ${tint.ring}55`,
          filter: "blur(0.5px)",
          opacity: 0.4 + intensity * 0.5,
          transition: "opacity 0.6s ease-out",
        }}
      />
      {/* A second, tighter rim just under the core, which is what reads as the
          orb resting on something rather than floating above a smudge. */}
      <div
        aria-hidden
        className="absolute left-1/2 -translate-x-1/2"
        style={{
          bottom: size * 0.055,
          width: size * 0.46,
          height: size * 0.1,
          borderRadius: "50%",
          border: `1px solid ${tint.ring}40`,
          background: `radial-gradient(ellipse at center, ${tint.ring}26 0%, transparent 70%)`,
          opacity: 0.35 + intensity * 0.5,
          transition: "opacity 0.6s ease-out",
        }}
      />

      {/* Halo. A blurred div rather than an SVG filter: filters re-rasterise
          every frame, and this one sits under a rotating element. */}
      <motion.div
        className="absolute rounded-full"
        animate={{ opacity: 0.25 + intensity * 0.45 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        style={{
          width: size * 0.92,
          height: size * 0.92,
          background: `radial-gradient(circle, ${tint.ring}55 0%, transparent 68%)`,
          filter: "blur(26px)",
        }}
      />

      <svg viewBox="0 0 400 400" className="absolute inset-0 size-full">
        <defs>
          <radialGradient id="orb-core">
            <stop offset="0%" stopColor={tint.core} />
            <stop offset="42%" stopColor={tint.ring} />
            <stop offset="100%" stopColor="#1D4ED8" />
          </radialGradient>
          <linearGradient id="orb-arc" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={tint.ring} />
            <stop offset="100%" stopColor="#2E7BFF" />
          </linearGradient>
        </defs>

        {/* Outer: ticks and a hairline, slowest. */}
        <motion.g style={{ rotate: outer, originX: "200px", originY: "200px" }}>
          <circle
            cx="200"
            cy="200"
            r="186"
            fill="none"
            stroke={tint.ring}
            strokeWidth="1"
            opacity={0.2 * intensity + 0.1}
          />
          <Ticks color={tint.ring} opacity={0.3 * intensity + 0.12} />
        </motion.g>

        {/* Middle: four bright arcs with gaps, counter-rotating. The signature
            element of the reference image. */}
        <motion.g style={{ rotate: mid, originX: "200px", originY: "200px" }}>
          {[0, 90, 180, 270].map((rotation) => (
            <path
              key={rotation}
              d="M 200 48 A 152 152 0 0 1 352 200"
              fill="none"
              stroke="url(#orb-arc)"
              strokeWidth="8.5"
              strokeLinecap="round"
              pathLength={1}
              strokeDasharray="0.74 0.26"
              opacity={0.4 + intensity * 0.6}
              transform={`rotate(${rotation} 200 200)`}
            />
          ))}
        </motion.g>

        {/* Inner: a thinner, faster broken ring, plus a solid hairline. The
            reference reads as four or five distinct concentric bands, so the
            layers are deliberately different weights rather than evenly
            spaced copies. */}
        <motion.g style={{ rotate: inner, originX: "200px", originY: "200px" }}>
          <circle
            cx="200"
            cy="200"
            r="118"
            fill="none"
            stroke={tint.ring}
            strokeWidth="3"
            strokeLinecap="round"
            pathLength={1}
            strokeDasharray="0.17 0.083"
            opacity={0.35 + intensity * 0.55}
          />
        </motion.g>
        <motion.g style={{ rotate: mid, originX: "200px", originY: "200px" }}>
          <circle
            cx="200"
            cy="200"
            r="102"
            fill="none"
            stroke={tint.ring}
            strokeWidth="1"
            pathLength={1}
            strokeDasharray="0.46 0.04"
            opacity={0.22 + intensity * 0.35}
          />
        </motion.g>

        {/* Core. Breathes via CSS so amplitude can change without restarting. */}
        <g
          className={animate ? "orb-breathe" : undefined}
          style={
            {
              transformOrigin: "200px 200px",
              "--orb-breathe": motionSpec.breathe,
            } as React.CSSProperties
          }
        >
          <circle
            cx="200"
            cy="200"
            r="86"
            fill="none"
            stroke={tint.ring}
            strokeWidth="1.5"
            opacity={0.25 * intensity + 0.1}
          />
          <circle
            cx="200"
            cy="200"
            r="66"
            fill="url(#orb-core)"
            opacity={0.3 + intensity * 0.6}
          />
          <circle
            cx="200"
            cy="200"
            r="66"
            fill="none"
            stroke={tint.core}
            strokeWidth="1"
            opacity={0.35 * intensity}
          />
          <text
            x="200"
            y="200"
            textAnchor="middle"
            dominantBaseline="central"
            fill="#FFFFFF"
            fontSize="34"
            fontWeight="700"
            letterSpacing="3"
            opacity={0.55 + intensity * 0.45}
            style={{ fontFamily: "var(--font-sans)" }}
          >
            IRA
          </text>
        </g>
      </svg>
    </div>
  );
}
