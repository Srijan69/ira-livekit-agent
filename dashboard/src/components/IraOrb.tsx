import { motion, type MotionValue } from "motion/react";
import type { CSSProperties, ReactNode } from "react";

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

type Tint = { ring: string; deep: string; core: string };

const TINT: Record<"default" | "offline" | "error", Tint> = {
  default: { ring: "#22D3EE", deep: "#2E7BFF", core: "#CFF8FF" },
  offline: { ring: "#64748B", deep: "#475569", core: "#B6C6D9" },
  error: { ring: "#F59E0B", deep: "#D97706", core: "#FFE9BF" },
};

const tintFor = (state: OrbState): Tint =>
  state === "offline" ? TINT.offline : state === "error" ? TINT.error : TINT.default;

/** A soft bloom for strokes. Costs nothing per frame: see `Layer`. */
function Glow({ id, blur }: { id: string; blur: number }) {
  return (
    <filter id={id} filterUnits="userSpaceOnUse" x="0" y="0" width="400" height="400">
      <feGaussianBlur stdDeviation={blur} result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>
  );
}

/**
 * One rotating ring, as its own HTML layer.
 *
 * Not an SVG `<g>`: Motion puts transformed SVG elements in
 * `transform-box: fill-box`, which measures the transform origin from each
 * group's own bounding box -- the rings rotated about points up to 82px off
 * centre. A full-size div rotates about its own centre, which is the orb's.
 *
 * It is also what makes the glow filters free. The div is a compositor layer,
 * so its SVG is rasterised once and the GPU rotates the cached texture; a
 * rotating `<g>` re-rasterises the whole SVG, filters included, every frame.
 */
function Layer({
  name,
  rotate,
  children,
}: {
  name: string;
  rotate: MotionValue<number>;
  children: ReactNode;
}) {
  return (
    <motion.div
      data-orb-layer={name}
      aria-hidden
      className="absolute inset-0"
      style={{ rotate, willChange: "transform" }}
    >
      <svg viewBox="0 0 400 400" className="size-full">
        {children}
      </svg>
    </motion.div>
  );
}

/** Evenly spaced tick marks, every fifth one long. */
function Ticks({ color, opacity }: { color: string; opacity: number }) {
  return (
    <g stroke={color} strokeWidth="1.5" opacity={opacity} strokeLinecap="round">
      {Array.from({ length: 72 }, (_, i) => {
        const angle = (i / 72) * Math.PI * 2;
        const long = i % 6 === 0;
        const inner = long ? 176 : 181;
        return (
          <line
            key={i}
            x1={200 + Math.cos(angle) * inner}
            y1={200 + Math.sin(angle) * inner}
            x2={200 + Math.cos(angle) * 186}
            y2={200 + Math.sin(angle) * 186}
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
 * Three counter-rotating ring layers around a breathing core, each its own
 * compositor layer, so every frame of motion is a GPU transform of a cached
 * texture. The breathing is a CSS keyframe with its amplitude in a custom
 * property, because changing a Motion keyframe array restarts the cycle on
 * every state change.
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
  const spec = MOTION[state];
  const tint = tintFor(state);
  const { intensity } = spec;

  const outer = useSpinner(spec.outer, animate);
  const mid = useSpinner(spec.mid, animate);
  const inner = useSpinner(spec.inner, animate);

  return (
    <div
      className="relative grid place-items-center"
      style={{ width: size, height: size }}
    >
      {/* The dais: concentric lit ellipses below the rings, as in the
          reference, so the orb reads as standing on something. */}
      <svg
        aria-hidden
        viewBox="0 0 400 110"
        className="absolute left-1/2 -translate-x-1/2"
        style={{
          bottom: -size * 0.075,
          width: size * 1.02,
          height: size * 0.28,
          opacity: 0.4 + intensity * 0.55,
          transition: "opacity 0.6s ease-out",
        }}
      >
        <defs>
          <radialGradient id="orb-dais-fill" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={tint.ring} stopOpacity="0.32" />
            <stop offset="60%" stopColor={tint.deep} stopOpacity="0.1" />
            <stop offset="100%" stopColor={tint.deep} stopOpacity="0" />
          </radialGradient>
        </defs>
        <ellipse cx="200" cy="55" rx="196" ry="50" fill="url(#orb-dais-fill)" />
        <ellipse cx="200" cy="55" rx="186" ry="44" fill="none" stroke={tint.ring} strokeOpacity="0.28" />
        <ellipse cx="200" cy="55" rx="140" ry="32" fill="none" stroke={tint.ring} strokeOpacity="0.4" strokeWidth="1.5" />
        <ellipse cx="200" cy="55" rx="96" ry="21" fill="none" stroke={tint.core} strokeOpacity="0.45" />
      </svg>

      {/* Halo. A blurred div rather than an SVG filter: it sits under every
          rotating layer and must never be re-rasterised with them. */}
      <motion.div
        aria-hidden
        className="absolute rounded-full"
        animate={{ opacity: 0.3 + intensity * 0.45 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        style={{
          width: size * 0.95,
          height: size * 0.95,
          background: `radial-gradient(circle, ${tint.ring}4D 0%, ${tint.deep}1F 45%, transparent 70%)`,
          filter: "blur(22px)",
        }}
      />

      {/* Outer: ticks and a hairline, slowest. */}
      <Layer name="outer" rotate={outer}>
        <circle
          cx="200"
          cy="200"
          r="191"
          fill="none"
          stroke={tint.ring}
          strokeWidth="1"
          opacity={0.12 + intensity * 0.18}
        />
        <Ticks color={tint.ring} opacity={0.14 + intensity * 0.3} />
        {/* Two faint long sweeps, so the slow layer has something to read
            as motion rather than a rotating ruler. */}
        <circle
          cx="200"
          cy="200"
          r="196"
          fill="none"
          stroke={tint.ring}
          strokeWidth="2"
          strokeLinecap="round"
          pathLength={1}
          strokeDasharray="0.16 0.34"
          opacity={0.18 + intensity * 0.3}
        />
      </Layer>

      {/* Middle: four bright arcs, counter-rotating -- the reference's
          signature element. */}
      <Layer name="mid" rotate={mid}>
        <defs>
          <linearGradient id="orb-arc" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={tint.core} />
            <stop offset="35%" stopColor={tint.ring} />
            <stop offset="100%" stopColor={tint.deep} />
          </linearGradient>
          <Glow id="orb-glow-mid" blur={4} />
        </defs>
        <g filter="url(#orb-glow-mid)">
          {[0, 90, 180, 270].map((rotation) => (
            <path
              key={rotation}
              d="M 200 50 A 150 150 0 0 1 350 200"
              fill="none"
              stroke="url(#orb-arc)"
              strokeWidth="9"
              strokeLinecap="round"
              pathLength={1}
              strokeDasharray="0.7 0.3"
              opacity={0.45 + intensity * 0.55}
              transform={`rotate(${rotation} 200 200)`}
            />
          ))}
        </g>
        <circle
          cx="200"
          cy="200"
          r="136"
          fill="none"
          stroke={tint.ring}
          strokeWidth="1"
          pathLength={1}
          strokeDasharray="0.46 0.04"
          opacity={0.2 + intensity * 0.3}
        />
      </Layer>

      {/* Inner: the reactor ring -- thick segments with even gaps -- and a
          dashed hairline, fastest. */}
      <Layer name="inner" rotate={inner}>
        <defs>
          <Glow id="orb-glow-inner" blur={3} />
        </defs>
        <circle
          cx="200"
          cy="200"
          r="104"
          fill="none"
          stroke={tint.ring}
          strokeWidth="13"
          pathLength={1}
          strokeDasharray="0.095 0.03"
          filter="url(#orb-glow-inner)"
          opacity={0.3 + intensity * 0.55}
        />
        <circle
          cx="200"
          cy="200"
          r="120"
          fill="none"
          stroke={tint.ring}
          strokeWidth="1.5"
          strokeLinecap="round"
          pathLength={1}
          strokeDasharray="0.02 0.03"
          opacity={0.25 + intensity * 0.4}
        />
      </Layer>

      {/* Core: dark, ringed in light, with the name glowing at its centre.
          Its own layer too, so the breathing scale composites rather than
          repaints. */}
      <div
        aria-hidden
        className={`absolute inset-0 ${animate ? "orb-breathe" : ""}`}
        style={{ "--orb-breathe": spec.breathe } as CSSProperties}
      >
        <svg viewBox="0 0 400 400" className="size-full">
          <defs>
            <radialGradient id="orb-core" cx="50%" cy="45%" r="55%">
              <stop offset="0%" stopColor="#0E2A47" />
              <stop offset="70%" stopColor="#071629" />
              <stop offset="100%" stopColor={tint.deep} stopOpacity="0.55" />
            </radialGradient>
            <Glow id="orb-glow-core" blur={5} />
            <Glow id="orb-glow-text" blur={6} />
          </defs>

          <circle cx="200" cy="200" r="84" fill="url(#orb-core)" />
          <circle
            cx="200"
            cy="200"
            r="86"
            fill="none"
            stroke={tint.ring}
            strokeWidth="3"
            filter="url(#orb-glow-core)"
            opacity={0.45 + intensity * 0.55}
          />
          <circle
            cx="200"
            cy="200"
            r="72"
            fill="none"
            stroke={tint.ring}
            strokeWidth="1"
            opacity={0.15 + intensity * 0.25}
          />
          <text
            x="200"
            y="202"
            textAnchor="middle"
            dominantBaseline="central"
            fill="#FFFFFF"
            fontSize="46"
            fontWeight="700"
            letterSpacing="4"
            filter="url(#orb-glow-text)"
            opacity={0.6 + intensity * 0.4}
            style={{ fontFamily: "var(--font-sans)" }}
          >
            IRA
          </text>
        </svg>
      </div>
    </div>
  );
}
