import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectionState } from "@/components/ConnectionBadge";
import { activeCuesFrom, orbStateFrom, type Cue, type OrbState } from "@/lib/orbState";

/**
 * The seam between the UI and IRA.
 *
 * Sub-project B replaces this file with `useLiveKitSession`, returning this
 * exact shape. Everything downstream -- the orb, the cues, the waveform, the
 * command bar -- is written against the type rather than against LiveKit, so
 * swapping the source touches one import in App.
 */
export type Session = {
  connection: ConnectionState;
  orbState: OrbState;
  cues: Cue[];
  micEnabled: boolean;
  toggleMic: () => void;
  /** Current output loudness, 0..1. Read once per animation frame. */
  getLevel: () => number;
  /** Sends text to IRA. Over `lk.chat` once there's a room. */
  send: (text: string) => void;
};

/** The demo loop: how long to hold each phase, in milliseconds. */
const SCRIPT: ReadonlyArray<{ phase: Phase; ms: number }> = [
  { phase: "connecting", ms: 1_800 },
  { phase: "listening", ms: 3_200 },
  { phase: "recognizing", ms: 2_600 },
  { phase: "generating", ms: 1_800 },
  { phase: "speaking", ms: 4_200 },
  { phase: "executing", ms: 2_200 },
  { phase: "speaking", ms: 2_600 },
];

type Phase =
  | "connecting"
  | "listening"
  | "recognizing"
  | "generating"
  | "speaking"
  | "executing";

/**
 * A scripted session, for sub-project A only.
 *
 * There is no LiveKit connection yet, so the orb would otherwise sit on one
 * state forever and the animation work would be unreviewable. This walks it
 * through every phase on a loop so the whole state machine is visible -- and
 * it drives the *real* `orbStateFrom`, not a parallel fake, so what you see is
 * the logic that ships.
 */
export function useMockSession(): Session {
  const [step, setStep] = useState(0);
  const [micEnabled, setMicEnabled] = useState(true);

  useEffect(() => {
    const current = SCRIPT[step % SCRIPT.length];
    const timer = setTimeout(
      () => setStep((n) => (n + 1) % SCRIPT.length),
      current.ms,
    );
    return () => clearTimeout(timer);
  }, [step]);

  const phase = SCRIPT[step % SCRIPT.length].phase;

  const input = useMemo(() => {
    const now = Date.now();
    return {
      connection: (phase === "connecting" ? "connecting" : "connected") as
        | "connecting"
        | "connected",
      agentPresent: phase !== "connecting",
      agentState:
        phase === "generating"
          ? ("thinking" as const)
          : phase === "speaking"
            ? ("speaking" as const)
            : ("listening" as const),
      userSpeaking: phase === "recognizing",
      lastStatus:
        phase === "executing"
          ? { state: "RUNNING", detail: "open_app", at: now }
          : null,
      now,
    };
  }, [phase]);

  const orbState = orbStateFrom(input);
  const cues = activeCuesFrom(input);

  // Synthetic loudness. Read through a ref so the waveform's frame loop never
  // triggers a React render -- the same contract the real audio analyser will
  // satisfy.
  const levelPhase = useRef(0);
  const getLevel = useCallback(() => {
    if (orbState !== "speaking" && orbState !== "recognizing") return 0;
    levelPhase.current += 0.055;
    const t = levelPhase.current;
    // Layered sines plus a slow envelope: speech is bursty, and a single sine
    // reads as a test tone.
    const envelope = 0.55 + 0.45 * Math.sin(t * 0.21);
    const burst = Math.abs(Math.sin(t) * 0.6 + Math.sin(t * 2.3) * 0.4);
    return Math.min(1, envelope * burst * (orbState === "speaking" ? 1 : 0.7));
  }, [orbState]);

  const toggleMic = useCallback(() => setMicEnabled((on) => !on), []);

  const send = useCallback((text: string) => {
    // Sub-project B replaces this with a `lk.chat` publish.
    console.info("[mock session] would send to IRA:", text);
  }, []);

  return {
    connection: input.connection,
    orbState,
    cues,
    micEnabled,
    toggleMic,
    getLevel,
    send,
  };
}
