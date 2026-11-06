export type ExecutionEvent = {
  state: string;
  detail?: string;
  /** Client arrival time in milliseconds, rather than the server clock. */
  at: number;
  run_id: string;
  call_id: string;
  tool_name?: string;
  source: "lifecycle" | "tool" | "skill";
  skill_run_id?: string;
  skill_slug?: string;
  skill_version?: number;
  step_index?: number;
  step_count?: number;
  step_tool?: string;
  step_outcome?: "running" | "verifying" | "waiting" | "done" | "error" | "cancelled" | "uncertain";
  outcome?: string;
};

export type ExecutionPhase = "running" | "verifying" | "waiting" | "finished" | "failed" | "cancelled" | "uncertain" | "unavailable";

export type ExecutionStep = {
  index: number;
  toolName: string;
  title: string;
  phase: ExecutionPhase;
  startedAt: number;
  updatedAt: number;
  endedAt: number | null;
};

export type ExecutionActivity = {
  id: string;
  runId: string;
  callId: string;
  toolName: string;
  title: string;
  phase: ExecutionPhase;
  skillRunId?: string;
  skillSlug?: string;
  skillVersion?: number;
  startedAt: number;
  updatedAt: number;
  endedAt: number | null;
  progress?: string;
  steps?: ExecutionStep[];
};

const STALE_AFTER_MS = 30_000;
const MAX_ACTIVE = 32;
const MAX_HISTORY = 8;
const MAX_STEPS = 32;

const STEP_TITLES: Record<string, string> = {
  ask: "Asking for clarification",
  wait: "Waiting for a condition",
  check: "Checking the result",
};

// Only fixed labels for tools registered on IraAgent may reach the panel.
// Arguments, results, exception text, and unknown names never become UI copy.
const TOOL_TITLES: Record<string, string> = {
  current_time: "Checking the time",
  add_task: "Saving a task",
  list_tasks: "Checking your tasks",
  complete_task: "Completing a task",
  system_status: "Checking your computer",
  open_app: "Opening an app",
  capture_screen: "Reviewing the screen",
  move_mouse: "Moving the pointer",
  click_mouse: "Clicking on the desktop",
  scroll_mouse: "Scrolling on the desktop",
  drag_mouse: "Dragging on the desktop",
  press_desktop_key: "Pressing a desktop key",
  desktop_move: "Moving the pointer",
  desktop_scroll: "Scrolling on the desktop",
  desktop_drag: "Dragging on the desktop",
  desktop_click: "Clicking on the desktop",
  desktop_type: "Typing on the desktop",
  desktop_press_key: "Pressing a desktop key",
  browser_click: "Selecting a page control",
  browser_type: "Typing on the page",
  type_on_desktop: "Typing on the desktop",
  browse_to: "Opening a page",
  open_spotify: "Opening Spotify",
  read_page: "Reading the page",
  list_page_elements: "Checking page controls",
  click_element: "Selecting a page control",
  type_into_element: "Typing on the page",
  go_back: "Going back a page",
  close_browser: "Closing the browser",
  save_workflow: "Saving a workflow",
  adapt_task: "Saving a repeatable task",
  list_workflows: "Checking saved workflows",
  predict_workflow: "Finding a workflow",
  open_workflows_folder: "Opening the workflows folder",
  run_workflow: "Running a workflow",
  run_skill: "Running a skill",
  answer_skill: "Resuming a skill",
  load_capabilities: "Loading available tools",
  enable_skill: "Enabling a skill",
  correct_memory: "Correcting a memory",
  stop_skill: "Stopping a skill",
  skill_status: "Checking skill progress",
  list_skills: "Checking saved skills",
  save_skill: "Saving a skill",
  delete_skill: "Archiving a skill",
  undo_skill_change: "Restoring a skill version",
  learn_skill: "Drafting a skill",
  delete_workflow: "Deleting a workflow",
  spotify_control: "Controlling Spotify",
  spotify_play_playlist: "Playing a Spotify playlist",
  remember: "Saving a memory",
  recall: "Checking your memories",
  forget: "Removing a memory",
  save_note: "Saving a note",
  remember_preference: "Saving a preference",
  list_preferences: "Checking your preferences",
};

export function safeToolName(name?: string): string {
  return name && Object.hasOwn(TOOL_TITLES, name) ? name : "";
}

export function isExecutionActive(item: { phase: ExecutionPhase }): boolean {
  return item.phase === "running" || item.phase === "verifying" || item.phase === "waiting";
}

const isActive = isExecutionActive;

function validTime(value: number): boolean {
  return Number.isFinite(value) && value >= 0;
}

function safeProgress(detail?: string): string | undefined {
  const text = detail?.trim();
  if (!text) return undefined;
  if (/^screen sent to Gemini$/i.test(text)) return "Screen shared for review";

  const steps = /^step (\d{1,4})\/(\d{1,4})$/i.exec(text)
    ?? /^(\d{1,4})\/(\d{1,4}) steps$/i.exec(text);
  if (!steps) return undefined;
  const step = Number(steps[1]);
  const total = Number(steps[2]);
  return step > 0 && step <= total ? `Step ${step} of ${total}` : undefined;
}

function finish(item: ExecutionActivity, phase: ExecutionActivity["phase"], now: number): ExecutionActivity {
  const endedAt = Math.max(now, item.updatedAt);
  return { ...item, phase, updatedAt: endedAt, endedAt, progress: undefined, steps: endActiveSteps(item.steps, phase, endedAt) };
}

function endActiveSteps(steps: ExecutionStep[] | undefined, phase: ExecutionPhase, at: number) {
  return steps?.map((step) => isActive(step)
    ? { ...step, phase: phase === "finished" ? "unavailable" as const : phase, endedAt: at }
    : step);
}

function trackStep(item: ExecutionActivity, event: ExecutionEvent): ExecutionStep[] | undefined {
  if (event.source !== "skill" || !Number.isInteger(event.step_index) || !Number.isInteger(event.step_count)
    || event.step_index! < 1 || event.step_index! > event.step_count! || event.step_count! > 10_000
    || !event.step_tool || !event.step_outcome) return item.steps;
  const steps = item.steps ?? [];
  const previous = steps.find((step) => step.index === event.step_index);
  // A late retransmission cannot undo a recorded step outcome.
  if (previous && (!isActive(previous) && previous.phase !== "unavailable")) return steps;
  const phase: ExecutionPhase = event.step_outcome === "done" ? "finished"
    : event.step_outcome === "error" ? "failed" : event.step_outcome;
  const toolName = previous?.toolName || safeToolName(event.step_tool);
  const stepTitle = Object.hasOwn(STEP_TITLES, event.step_tool) ? STEP_TITLES[event.step_tool] : "Running a skill step";
  const step: ExecutionStep = {
    index: event.step_index!, toolName, title: TOOL_TITLES[toolName] ?? stepTitle, phase,
    startedAt: previous?.startedAt ?? event.at, updatedAt: event.at,
    endedAt: isActive({ phase }) ? null : event.at,
  };
  return [...steps.filter((entry) => entry.index !== step.index), step]
    .sort((a, b) => a.index - b.index).slice(-MAX_STEPS);
}

/** Preserve insertion order, all retained active calls, and the latest terminal history. */
function boundExecution(items: ExecutionActivity[], now: number): ExecutionActivity[] {
  const active = items.filter(isActive);
  let bounded = items;
  if (active.length > MAX_ACTIVE) {
    const overflow = new Set(
      [...active]
        .sort((a, b) => a.updatedAt - b.updatedAt)
        .slice(0, active.length - MAX_ACTIVE)
        .map((item) => item.id),
    );
    bounded = items.map((item) => overflow.has(item.id)
      ? { ...item, phase: "unavailable", endedAt: Math.max(now, item.updatedAt), progress: undefined, steps: endActiveSteps(item.steps, "unavailable", now) }
      : item);
  }

  const history = bounded.filter((item) => !isActive(item));
  if (history.length <= MAX_HISTORY) return bounded;
  const retained = new Set(
    [...history]
      .reverse()
      .sort((a, b) => (b.endedAt ?? b.updatedAt) - (a.endedAt ?? a.updatedAt))
      .slice(0, MAX_HISTORY)
      .map((item) => item.id),
  );
  return bounded.filter((item) => isActive(item) || retained.has(item.id));
}

/**
 * SDK starts establish call identity. Tool reports only amend an established call.
 * Generic completion cannot erase a specific failure or cancellation, and terminal
 * retransmissions cannot reopen work. A timeout remains recoverable by a real update.
 */
export function trackExecution(items: ExecutionActivity[], event: ExecutionEvent): ExecutionActivity[] {
  if (!validTime(event.at) || !event.run_id.trim() || !event.call_id.trim()) return items;
  if (!["lifecycle", "tool", "skill"].includes(event.source)) return items;
  if (event.source === "skill" && (typeof event.skill_run_id !== "string" || !event.skill_run_id.trim() || event.skill_run_id.length > 128)) return items;

  const state = event.state.trim().toUpperCase();
  const waiting = event.source === "skill" && event.outcome === "waiting" && state === "LISTENING";
  if (!waiting && !["DELEGATING", "RUNNING", "VERIFYING", "DONE", "ERROR", "CANCELLED"].includes(state)) {
    return items;
  }

  const id = JSON.stringify([event.run_id, event.call_id]);
  const skillIndex = event.source === "skill"
    ? items.findIndex((item) => item.runId === event.run_id && item.skillRunId === event.skill_run_id) : -1;
  const index = skillIndex >= 0 ? skillIndex : items.findIndex((item) => item.id === id);
  if (index === -1) {
    if (event.source !== "lifecycle" || state !== "DELEGATING") return items;
    const toolName = safeToolName(event.tool_name);
    const next = items.map((item) => isActive(item) && item.runId !== event.run_id
      ? finish(item, "cancelled", event.at)
      : item);
    next.push({
      id,
      runId: event.run_id,
      callId: event.call_id,
      toolName,
      title: TOOL_TITLES[toolName] ?? "Working on your request",
      phase: "running",
      startedAt: event.at,
      updatedAt: event.at,
      endedAt: null,
    });
    return boundExecution(next, event.at);
  }

  const original = items[index];
  // SDK completion closes only the outer tool invocation. A skill can be
  // waiting for input and resume later through a different answer_skill call.
  if (original.skillRunId && event.source !== "skill" && state === "DONE") return items;
  if (event.skill_run_id && original.skillRunId && event.skill_run_id !== original.skillRunId) return items;
  let item = event.source === "skill" ? {
    ...original,
    skillRunId: event.skill_run_id,
    skillSlug: event.skill_slug ?? original.skillSlug,
    skillVersion: event.skill_version ?? original.skillVersion,
  } : original;
  if (event.at < item.updatedAt) return items;
  if (item.phase === "failed" || item.phase === "cancelled" || item.phase === "uncertain") return items;
  if (item.phase === "finished" && state !== "ERROR" && state !== "CANCELLED") return items;
  if (item.phase === "unavailable" && state === "DELEGATING") return items;
  item = { ...item, steps: trackStep(item, event) };

  let nextItem: ExecutionActivity;
  if (state === "DONE" || state === "ERROR" || state === "CANCELLED") {
    const phase = event.outcome === "uncertain" ? "uncertain"
      : event.outcome === "cancelled" || state === "CANCELLED" ? "cancelled"
      : event.outcome === "error" || state === "ERROR" ? "failed" : "finished";
    nextItem = finish(item, phase, event.at);
    if (state === "DONE" && item.toolName === "open_app") nextItem.progress = "Request sent";
  } else {
    nextItem = {
      ...item,
      phase: waiting ? "waiting" : state === "VERIFYING" ? "verifying" : state === "DELEGATING" ? item.phase : "running",
      updatedAt: event.at,
      endedAt: null,
      progress: Number.isInteger(event.step_index) && Number.isInteger(event.step_count)
        && event.step_index! > 0 && event.step_index! <= event.step_count! && event.step_count! <= 10_000
        ? `Step ${event.step_index} of ${event.step_count}` : safeProgress(event.detail) ?? item.progress,
    };
  }

  const next = items.slice();
  next[index] = nextItem;
  return boundExecution(next, event.at);
}

/** A missing end event means the current status is unknown, never successful. */
export function expireExecution(items: ExecutionActivity[], now: number): ExecutionActivity[] {
  if (!validTime(now)) return items;
  let changed = false;
  const next = items.map((item): ExecutionActivity => {
    if (!isActive(item) || item.phase === "waiting" || now - item.updatedAt < STALE_AFTER_MS) return item;
    changed = true;
    return { ...item, phase: "unavailable", endedAt: now, progress: undefined, steps: endActiveSteps(item.steps, "unavailable", now) };
  });
  return changed ? boundExecution(next, now) : items;
}

/** Use when the session disconnects, stops, or restarts. */
export function interruptExecution(items: ExecutionActivity[], now: number, phase: "cancelled" | "unavailable" = "cancelled"): ExecutionActivity[] {
  if (!validTime(now)) return items;
  let changed = false;
  const next = items.map((item) => {
    if (!isActive(item)) return item;
    changed = true;
    return phase === "unavailable" ? {...item, phase, endedAt: now, progress: undefined, steps: endActiveSteps(item.steps, phase, now)} : finish(item, phase, now);
  });
  return changed ? boundExecution(next, now) : items;
}

export function clearFinishedExecution(items: ExecutionActivity[]): ExecutionActivity[] {
  const active = items.filter(isActive);
  return active.length === items.length ? items : active;
}
