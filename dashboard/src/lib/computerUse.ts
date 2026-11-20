import type { ComputerObservationEvent } from "@shared/computerUse";
export type ComputerFrame = { event: ComputerObservationEvent; receivedAt: number };
export type ComputerTimelineEntry = { callId: string; toolName: string; action?: ComputerObservationEvent["action"]; phase: ComputerObservationEvent["phase"]; at: number };
export type ComputerUseState = {
  runId: string | null; sequence: number; phase: ComputerObservationEvent["phase"] | null;
  before: ComputerFrame | null; latest: ComputerFrame | null;
  timeline: ComputerTimelineEntry[]; retiredRuns: string[];
};
export const EMPTY_COMPUTER_USE: ComputerUseState = { runId: null, sequence: -1, phase: null, before: null, latest: null, timeline: [], retiredRuns: [] };
export function trackComputerUse(state: ComputerUseState, event: ComputerObservationEvent, at: number): ComputerUseState {
  if (!event || !Number.isFinite(at) || at < 0 || state.retiredRuns.includes(event.run_id)
    || (event.run_id === state.runId && event.sequence <= state.sequence)) return state;
  const changedRun = !!state.runId && event.run_id !== state.runId;
  const previous = changedRun ? EMPTY_COMPUTER_USE : state;
  const retiredRuns = changedRun ? [...state.retiredRuns, state.runId!].slice(-8) : state.retiredRuns;
  const next = { ...previous, runId: event.run_id, sequence: event.sequence, phase: event.phase, retiredRuns };
  if (event.phase === "cleared") return { ...next, before: null, latest: null, timeline: [] };
  const entry: ComputerTimelineEntry = { callId: event.call_id, toolName: event.tool_name,
    action: event.action ?? previous.timeline.find((item) => item.callId === event.call_id)?.action, phase: event.phase, at };
  next.timeline = [entry, ...previous.timeline.filter((item) => item.callId !== event.call_id)].slice(0, 6);
  if (event.screenshot_data_url && event.image_size) {
    const frame = { event, receivedAt: at };
    const sameCall = previous.latest?.event.call_id === event.call_id;
    next.before = event.phase === "before" ? frame : sameCall ? previous.before : null;
    next.latest = frame;
  }
  return next;
}
export function computerUseStatus(state: ComputerUseState, connected: boolean, now: number): "disconnected" | "empty" | "stale" | "unavailable" | "observed" {
  if (!connected) return "disconnected";
  if (state.phase === "unavailable") return "unavailable";
  if (!state.latest) return "empty";
  const capturedAt = Date.parse(state.latest.event.captured_at ?? "");
  if (!Number.isFinite(capturedAt) || capturedAt > now + 5_000
    || now - capturedAt >= 30_000 || now - state.latest.receivedAt >= 30_000) return "stale";
  return "observed";
}

export const COMPUTER_ACTION_LABELS = {
  move: "Moving pointer", click: "Clicking", click_target: "Selecting control", scroll: "Scrolling",
  drag: "Dragging", type: "Typing", key: "Pressing key", focus: "Focusing window",
} as const;
