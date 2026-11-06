import { motion, type MotionValue } from "motion/react";
import {
  useId,
  type CSSProperties,
  type ReactNode,
  type SVGProps,
} from "react";

import { useSpinner } from "@/hooks/useSpinner";
import type { OrbState } from "@/lib/orbState";

// Rates preserve the current angle when a session transition changes speed.
const MOTION: Record<
  OrbState,
  {
    outer: number;
    mid: number;
    inner: number;
    intensity: number;
    breathe: number;
  }
> = {
  offline: {
    outer: 0.6,
    mid: -0.9,
    inner: 1.5,
    intensity: 0.22,
    breathe: 1.004,
  },
  connecting: { outer: 4, mid: -6, inner: 10, intensity: 0.45, breathe: 1.02 },
  listening: { outer: 6, mid: -9, inner: 15, intensity: 0.75, breathe: 1.03 },
  recognizing: {
    outer: 9,
    mid: -13,
    inner: 20,
    intensity: 0.95,
    breathe: 1.02,
  },
  generating: {
    outer: 15,
    mid: -22.5,
    inner: 37.5,
    intensity: 1,
    breathe: 1.015,
  },
  speaking: { outer: 8, mid: -12, inner: 22, intensity: 1, breathe: 1.042 },
  executing: { outer: 11, mid: -16, inner: 26, intensity: 0.9, breathe: 1.025 },
  error: { outer: 6, mid: -9, inner: 15, intensity: 0.85, breathe: 1.03 },
};

type Tint = { ring: string; deep: string; core: string };
const TINT: Record<"default" | "offline" | "error", Tint> = {
  default: {
    ring: "var(--orb-ring)",
    deep: "var(--orb-deep)",
    core: "var(--orb-core)",
  },
  offline: { ring: "#64748B", deep: "#475569", core: "#B6C6D9" },
  error: { ring: "#F59E0B", deep: "#D97706", core: "#FFE9BF" },
};

function Glow({ id, blur }: { id: string; blur: number }) {
  return (
    <filter
      id={id}
      filterUnits="userSpaceOnUse"
      x="0"
      y="0"
      width="400"
      height="400"
    >
      <feGaussianBlur stdDeviation={blur} result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>
  );
}

function Ring({
  radius,
  ...props
}: SVGProps<SVGCircleElement> & { radius: number }) {
  return <circle cx="200" cy="200" r={radius} fill="none" {...props} />;
}

// Rotate full-size HTML layers about the orb centre. Motion's SVG transforms
// use a group's own bounding box, causing asymmetric arcs to orbit off-centre.
// HTML layers also let Chromium composite cached strokes and glow textures.
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

function Ticks({ color }: { color: string }) {
  return (
    <g stroke={color} strokeWidth="0.8">
      {Array.from({ length: 180 }, (_, i) => {
        const angle = (i / 180) * Math.PI * 2;
        const major = i % 15 === 0;
        const inner = major ? 149 : i % 5 === 0 ? 153 : 156;
        return (
          <line
            key={i}
            x1={200 + Math.cos(angle) * inner}
            y1={200 + Math.sin(angle) * inner}
            x2={200 + Math.cos(angle) * 161}
            y2={200 + Math.sin(angle) * 161}
            opacity={major ? 0.95 : i % 5 === 0 ? 0.7 : 0.35}
          />
        );
      })}
    </g>
  );
}

/** Three counter-rotating mechanical bands around a breathing, luminous core. */
export function IraOrb({
  state,
  animate,
  size = 300,
  showBase = true,
}: {
  state: OrbState;
  /** False freezes rotation and breathing for reduced motion / hidden windows. */
  animate: boolean;
  size?: number | string;
  /** Omit when the background already supplies the physical platform. */
  showBase?: boolean;
}) {
  const spec = MOTION[state];
  const tint =
    state === "offline"
      ? TINT.offline
      : state === "error"
        ? TINT.error
        : TINT.default;
  const { intensity } = spec;
  const outer = useSpinner(spec.outer, animate);
  const mid = useSpinner(spec.mid, animate);
  const inner = useSpinner(spec.inner, animate);
  // Unique paint servers also support multiple orbs in previews / future views.
  const id = useId();
  const paint = (name: string) => `url(#${id}-${name})`;
  const light = 0.3 + intensity * 0.7;

  return (
    <div
      className="relative grid place-items-center"
      style={{ width: size, height: size }}
    >
      <motion.div
        aria-hidden
        className="absolute rounded-full"
        animate={{ opacity: 0.2 + intensity * 0.35 }}
        transition={{ duration: 0.6 }}
        style={{
          width: "110%",
          height: "110%",
          background: `radial-gradient(circle, color-mix(in srgb, ${tint.ring} 16%, transparent), color-mix(in srgb, ${tint.deep} 8%, transparent) 48%, transparent 70%)`,
          filter: "blur(16px)",
        }}
      />

      {/* A low, solid plinth with luminous rims gives the rings depth. */}
      {showBase && (
        <svg
          aria-hidden
          viewBox="0 0 440 100"
          className="absolute left-1/2 -translate-x-1/2"
          style={{
            bottom: "-6.5%",
            width: "112%",
            height: "24%",
          }}
        >
          <defs>
            <radialGradient id={`${id}-plinth`}>
              <stop offset="0" stopColor={tint.deep} stopOpacity="0.22" />
              <stop offset="0.75" stopColor="var(--color-mid)" />
              <stop offset="1" stopColor="var(--color-deep)" />
            </radialGradient>
            <linearGradient id={`${id}-rim`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor={tint.deep} stopOpacity="0.2" />
              <stop offset="0.3" stopColor={tint.ring} />
              <stop offset="0.5" stopColor={tint.core} />
              <stop offset="0.7" stopColor={tint.ring} />
              <stop offset="1" stopColor={tint.deep} stopOpacity="0.2" />
            </linearGradient>
          </defs>
          <ellipse
            cx="220"
            cy="60"
            rx="208"
            ry="31"
            fill="var(--color-deep)"
            stroke={tint.deep}
            strokeOpacity="0.22"
          />
          <ellipse
            cx="220"
            cy="52"
            rx="204"
            ry="28"
            fill={paint("plinth")}
            stroke={paint("rim")}
            strokeWidth="2"
            opacity={light}
          />
          <ellipse
            cx="220"
            cy="49"
            rx="183"
            ry="22"
            fill="none"
            stroke={tint.ring}
            strokeOpacity={light * 0.3}
          />
          <ellipse
            cx="220"
            cy="48"
            rx="135"
            ry="15"
            fill="none"
            stroke={tint.core}
            strokeOpacity={light * 0.32}
          />
          <path
            d="M 30 61 Q 220 115 410 61 M 94 67 L 106 74 H 167 M 273 74 H 334 L 346 67"
            fill="none"
            stroke={paint("rim")}
            strokeWidth="2.5"
            opacity={light}
          />
        </svg>
      )}

      {/* Stationary housing and shared paints preserve the selected theme's
          dark body, secondary bands, and bright primary edges. */}
      <svg
        aria-hidden
        viewBox="0 0 400 400"
        className="absolute inset-0 size-full"
      >
        <defs>
          <radialGradient id={`${id}-housing`}>
            <stop offset="0" stopColor="var(--color-deep)" />
            <stop offset="0.58" stopColor="var(--color-mid)" />
            <stop offset="0.74" stopColor="var(--color-surface)" />
            <stop offset="0.81" stopColor="var(--color-deep)" />
            <stop offset="0.95" stopColor="var(--color-surface)" />
            <stop offset="1" stopColor="var(--color-deep)" />
          </radialGradient>
          <linearGradient id={`${id}-band`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={tint.ring} />
            <stop offset="0.48" stopColor={tint.deep} />
            <stop offset="0.72" stopColor={tint.ring} />
            <stop offset="1" stopColor={tint.deep} />
          </linearGradient>
          <linearGradient id={`${id}-segment`} x1="0" y1="1" x2="1" y2="0">
            <stop offset="0" stopColor={tint.deep} />
            <stop offset="0.48" stopColor={tint.ring} />
            <stop offset="0.72" stopColor={tint.core} />
            <stop offset="1" stopColor={tint.ring} />
          </linearGradient>
          <radialGradient id={`${id}-core`} cx="45%" cy="40%" r="65%">
            <stop offset="0" stopColor="var(--color-surface)" />
            <stop offset="0.76" stopColor="var(--color-deep)" />
            <stop offset="1" stopColor={tint.deep} stopOpacity="0.4" />
          </radialGradient>
          <Glow id={`${id}-glow`} blur={2} />
          <Glow id={`${id}-text-glow`} blur={3} />
        </defs>
        <circle cx="200" cy="200" r="190" fill={paint("housing")} />
        <Ring
          radius={193}
          stroke={tint.ring}
          opacity={0.12 + intensity * 0.1}
        />
        <Ring radius={188} stroke={tint.deep} strokeWidth="2" opacity="0.3" />
        <Ring radius={170} stroke={tint.ring} opacity="0.18" />
        <Ring radius={142} stroke={tint.deep} opacity="0.25" />
        <Ring radius={127} stroke={tint.ring} opacity="0.25" />
        <Ring radius={93} stroke={tint.deep} strokeWidth="5" opacity="0.3" />
        <g stroke={tint.ring} strokeWidth="0.65" opacity="0.18">
          <path d="M200 8 V48 M200 352 V390 M8 200 H49 M351 200 H392" />
          <Ring radius={137} />
        </g>
      </svg>

      <Layer name="outer" rotate={outer}>
        <g opacity={light}>
          <Ring
            radius={181}
            stroke={tint.deep}
            strokeWidth="12"
            opacity="0.2"
          />
          <Ring
            radius={181}
            stroke={paint("band")}
            strokeWidth="8"
            pathLength={1}
            strokeDasharray="0.22 0.035 0.14 0.12 0.24 0.245"
            strokeDashoffset="0.06"
          />
          <Ring
            radius={186}
            stroke={tint.ring}
            strokeWidth="1.2"
            pathLength={1}
            strokeDasharray="0.22 0.035 0.14 0.12 0.24 0.245"
            strokeDashoffset="0.06"
            filter={paint("glow")}
          />
          <Ring
            radius={175}
            stroke={tint.core}
            strokeWidth="1"
            pathLength={1}
            strokeDasharray="0.08 0.02 0.12 0.28 0.16 0.34"
            opacity="0.65"
          />
          <Ring
            radius={197}
            stroke={tint.ring}
            strokeWidth="1.5"
            pathLength={1}
            strokeDasharray="0.045 0.955"
            strokeDashoffset="0.08"
            filter={paint("glow")}
          />
          {[0, 26, 88, 152, 181, 242, 306].map((angle) => (
            <path
              key={angle}
              d="M200 14 V28"
              stroke="var(--color-deep)"
              strokeWidth="2"
              transform={`rotate(${angle} 200 200)`}
              opacity="0.8"
            />
          ))}
        </g>
      </Layer>

      <Layer name="mid" rotate={mid}>
        <g opacity={light}>
          <Ticks color={tint.ring} />
          <Ring
            radius={164}
            stroke={tint.deep}
            strokeWidth="2"
            pathLength={1}
            strokeDasharray="0.31 0.025 0.19 0.12 0.17 0.185"
          />
          <Ring
            radius={151}
            stroke={tint.ring}
            strokeWidth="0.65"
            opacity="0.3"
          />
          <Ring
            radius={136}
            stroke={tint.ring}
            strokeWidth="1"
            pathLength={1}
            strokeDasharray="0.17 0.08 0.35 0.15 0.1 0.15"
            opacity="0.55"
          />
          <Ring
            radius={131}
            stroke={tint.core}
            strokeWidth="1"
            pathLength={1}
            strokeDasharray="0.07 0.43"
            opacity="0.65"
          />
        </g>
      </Layer>

      <Layer name="inner" rotate={inner}>
        <g opacity={light}>
          <Ring radius={111} stroke="var(--color-deep)" strokeWidth="28" />
          <Ring radius={124} stroke={tint.deep} strokeWidth="2" />
          <Ring
            radius={97}
            stroke={tint.ring}
            strokeWidth="0.8"
            opacity="0.5"
          />
          {Array.from({ length: 8 }, (_, i) => (
            <g key={i} transform={`rotate(${i * 45 + 4} 200 200)`}>
              <path
                d="M200 89 A111 111 0 0 1 260.45 106.91"
                fill="none"
                stroke={tint.deep}
                strokeWidth="23"
                opacity="0.55"
              />
              <path
                d="M200 89 A111 111 0 0 1 260.45 106.91"
                fill="none"
                stroke={paint("segment")}
                strokeWidth="16"
                filter={paint("glow")}
              />
              <path
                d="M200 83 A117 117 0 0 1 263.72 101.87"
                fill="none"
                stroke={tint.core}
                strokeWidth="2"
                opacity="0.75"
              />
              <path
                d="M194 78 L186 79 L189 101 L195 100 Z"
                fill="var(--color-mid)"
                stroke={tint.ring}
                strokeWidth="0.5"
                strokeOpacity="0.35"
              />
            </g>
          ))}
        </g>
      </Layer>

      <div
        aria-hidden
        className={`absolute inset-0 ${animate ? "orb-breathe" : ""}`}
        style={{ "--orb-breathe": spec.breathe } as CSSProperties}
      >
        <svg viewBox="0 0 400 400" className="size-full">
          <circle cx="200" cy="200" r="83" fill={paint("core")} />
          <Ring
            radius={82}
            stroke={tint.deep}
            strokeWidth="6"
            opacity={light * 0.35}
          />
          <Ring
            radius={77}
            stroke={tint.ring}
            strokeWidth="2.5"
            filter={paint("glow")}
            opacity={light}
          />
          <Ring
            radius={75}
            stroke={tint.core}
            strokeWidth="0.8"
            opacity={light}
          />
          <Ring
            radius={65}
            stroke={tint.ring}
            strokeWidth="0.6"
            opacity={light * 0.2}
          />
          <text
            x="202"
            y="202"
            textAnchor="middle"
            dominantBaseline="central"
            fill={tint.core}
            fontSize="41"
            fontWeight="600"
            letterSpacing="3"
            filter={paint("text-glow")}
            opacity={0.65 + intensity * 0.35}
            style={{ fontFamily: "var(--font-sans)" }}
          >
            IRA
          </text>
        </svg>
      </div>
    </div>
  );
}
