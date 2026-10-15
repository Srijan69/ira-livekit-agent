import { AnimatePresence, motion } from "motion/react";
import { useRef } from "react";

import { IraOrb } from "./IraOrb";
import { Waveform } from "./Waveform";
import {
  LEFT_CUES,
  RIGHT_CUES,
  orbLabel,
  type Cue,
  type OrbState,
} from "@/lib/orbState";

/**
 * Abstract line art, where the reference image has a borrowed character.
 *
 * The decision is recorded in the Python suite: a test asserts IRA's system
 * prompt never names a borrowed character, on the reasoning that she should
 * behave like that assistant rather than be her. Putting the character in the
 * UI would reverse that decision somewhere the tests don't reach.
 */
function QuotePanel() {
  return (
    // Absolutely positioned so it costs the layout no width. Laid out in flow
    // it needed a matching spacer on the right to keep the orb centred, and
    // the pair together overflowed the stage at the default window size --
    // which is why it vanished entirely rather than just moving.
    //
    // The 800px threshold is sized so the centred orb group leaves more than
    // this panel's width of clear space on each side. Below it, the panel
    // hides rather than overlapping the cue column.
    <div className="pointer-events-none absolute top-1/2 left-0 hidden w-[150px] -translate-y-1/2 flex-col gap-4 @[800px]:flex">
      <svg viewBox="0 0 160 190" className="h-[164px] w-full" aria-hidden>
        <g stroke="#2E7BFF" fill="none" strokeWidth="0.9" opacity="0.5">
          <circle cx="80" cy="62" r="34" />
          <circle cx="80" cy="62" r="22" opacity="0.6" />
          <circle cx="80" cy="62" r="9" opacity="0.8" />
          <path d="M80 28 V8 M80 96 v22 M46 62 H18 M114 62 h28" />
          <path d="M56 38 L38 20 M104 38 l18 -18 M56 86 l-18 18 M104 86 l18 18" />
          <path d="M18 62 H8 v54 h40" opacity="0.45" />
          <path d="M142 62 h10 v54 h-40" opacity="0.45" />
          <path d="M48 118 h64" opacity="0.6" />
          <path d="M60 134 h40 M68 148 h24" opacity="0.4" />
        </g>
        <g fill="#22D3EE" opacity="0.75">
          {[
            [80, 8],
            [18, 62],
            [142, 62],
            [80, 118],
            [38, 20],
            [122, 20],
          ].map(([cx, cy]) => (
            <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="2" />
          ))}
        </g>
      </svg>

      <blockquote className="px-1">
        <p className="text-[15px] leading-snug font-light text-text/80 italic">
          &ldquo;The answer first, boss. Always.&rdquo;
        </p>
        <footer className="label-caps mt-2.5">&mdash; IRA</footer>
      </blockquote>
    </div>
  );
}

/**
 * The THINK / ANALYZE / RESPOND and UNDERSTAND / LEARN / EXECUTE columns.
 *
 * Live, not decorative. In the reference image these six words are static
 * chrome; here each one lights when IRA is actually doing that thing, which is
 * the difference between a readout and a sticker.
 */
function CueColumn({
  cues,
  active,
  align,
}: {
  cues: readonly Cue[];
  active: Cue[];
  align: "left" | "right";
}) {
  return (
    <ul
      className={`hidden shrink-0 space-y-3 @[700px]:block ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {cues.map((cue) => {
        const isActive = active.includes(cue);
        return (
          <li key={cue}>
            <motion.span
              className="label-caps inline-flex items-center gap-1.5 whitespace-nowrap"
              animate={{
                color: isActive ? "#22D3EE" : "#5A7290",
                opacity: isActive ? 1 : 0.65,
              }}
              transition={{ duration: 0.3 }}
              style={{
                flexDirection: align === "right" ? "row" : "row-reverse",
                textShadow: isActive ? "0 0 12px rgb(34 211 238 / 0.55)" : "none",
              }}
            >
              <span>{cue}</span>
              {align === "right" && (
                <motion.span
                  aria-hidden
                  className="text-[9px]"
                  animate={{ opacity: isActive ? 1 : 0.4 }}
                >
                  +
                </motion.span>
              )}
            </motion.span>
          </li>
        );
      })}
    </ul>
  );
}

export function Stage({
  state,
  cues,
  animate,
  getLevel,
}: {
  state: OrbState;
  cues: Cue[];
  animate: boolean;
  getLevel: () => number;
}) {
  // `orbLabel` returns "" for the error flash on purpose -- an amber flash is
  // a flash, and swapping the caption to ERROR for a second and a half reads
  // as a crash. Keep whatever was showing.
  const label = orbLabel(state);
  const lastLabel = useRef(label);
  if (label) lastLabel.current = label;
  const shown = label || lastLabel.current;

  const waveActive = state === "speaking" || state === "recognizing";

  return (
    // `@container` plus `min-w-0`: the side columns are gated on how much room
    // the stage itself has, not on the window width. Gating them on `xl:`
    // sized them against the viewport while they live in a column roughly 820px
    // wide -- which overflowed and pushed the right-hand rail off screen.
    //
    // No horizontal padding here, deliberately: a container query measures the
    // *content* box, so `px-2` would silently shrink what the thresholds below
    // compare against by 16px.
    <div className="@container relative flex min-h-0 min-w-0 flex-1 items-center justify-center gap-7">
      <QuotePanel />
      <CueColumn cues={LEFT_CUES} active={cues} align="left" />

      <div className="flex shrink-0 flex-col items-center justify-center">
        <IraOrb state={state} animate={animate} size={264} />

        <div className="mt-5 flex h-4 items-center">
          <AnimatePresence mode="wait">
            <motion.p
              key={shown}
              initial={{ opacity: 0, y: 3 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -3 }}
              transition={{ duration: 0.25 }}
              className="label-caps !tracking-[0.3em]"
              style={
                state === "offline"
                  ? undefined
                  : { color: "#8FA6C4", textShadow: "0 0 14px rgb(34 211 238 / 0.3)" }
              }
            >
              {shown}
            </motion.p>
          </AnimatePresence>
        </div>

        <div className="mt-3">
          <Waveform getLevel={getLevel} active={waveActive && animate} />
        </div>
      </div>

      <CueColumn cues={RIGHT_CUES} active={cues} align="right" />
    </div>
  );
}
