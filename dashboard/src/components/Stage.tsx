import { AnimatePresence, motion } from "motion/react";
import { useRef } from "react";

import { IraOrb } from "./IraOrb";
import { Waveform } from "./Waveform";
import { ExecutionStatus } from "./ExecutionStatus";
import type { ExecutionActivity } from "@/lib/execution";
import {
  LEFT_CUES,
  RIGHT_CUES,
  orbLabel,
  type Cue,
  type OrbState,
} from "@/lib/orbState";

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
      className={`hidden shrink-0 -translate-y-12 space-y-3 @[780px]:block ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {cues.map((cue) => {
        const isActive = active.includes(cue);
        return (
          <li key={cue}>
            <motion.span
              className="label-caps inline-flex items-center gap-1.5 whitespace-nowrap transition-colors duration-300"
              animate={{
                opacity: isActive ? 1 : 0.85,
              }}
              transition={{ duration: 0.3 }}
              style={{
                color: isActive ? "var(--color-cyan)" : "var(--color-dim)",
                flexDirection: align === "right" ? "row" : "row-reverse",
                textShadow: isActive
                  ? "0 0 12px rgb(var(--accent-rgb) / 0.55), 0 1px 5px var(--color-deep)"
                  : "0 1px 5px var(--color-deep), 0 0 12px var(--color-deep)",
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
  activities,
  now,
  onClearExecution,
}: {
  state: OrbState;
  cues: Cue[];
  animate: boolean;
  getLevel: () => number;
  activities: ExecutionActivity[];
  now: number;
  onClearExecution: () => void;
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
    <div
      className="execution-stage @container flex min-h-0 min-w-0 flex-1 items-center gap-4"
      style={{ containerType: "size" }}
    >
      <div className="execution-stage-layout">
        <ExecutionStatus
          activities={activities}
          state={state}
          now={now}
          onClear={onClearExecution}
          animate={animate}
        />

        {/* Keep the execution timeline alongside the live orb. */}
        <div className="stage-orb-group flex min-w-0 flex-1 items-center justify-center gap-5">
          <CueColumn cues={LEFT_CUES} active={cues} align="left" />

          <div className="stage-orb-visual flex shrink-0 flex-col items-center justify-center">
            {/* Reserve room for the caption and waveform in shorter windows. */}
            <IraOrb
              state={state}
              animate={animate}
              size="var(--stage-orb-size)"
              showBase={false}
            />

            <div className="mt-4 flex h-4 items-center">
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
                      : {
                          color: "var(--color-dim)",
                          textShadow: "0 0 14px rgb(var(--accent-rgb) / 0.3)",
                        }
                  }
                >
                  {shown}
                </motion.p>
              </AnimatePresence>
            </div>

            <div className="stage-waveform mt-3">
              <Waveform
                getLevel={getLevel}
                active={waveActive && animate}
                width={380}
                height={52}
              />
            </div>
          </div>

          <CueColumn cues={RIGHT_CUES} active={cues} align="right" />
        </div>
      </div>
    </div>
  );
}
