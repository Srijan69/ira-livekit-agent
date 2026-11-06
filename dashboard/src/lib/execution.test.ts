import { describe, expect, it } from "vitest";

import {
  clearFinishedExecution,
  expireExecution,
  interruptExecution,
  trackExecution,
  type ExecutionActivity,
  type ExecutionEvent,
} from "./execution";

const event = (
  state: string,
  at: number,
  patch: Partial<ExecutionEvent> = {},
): ExecutionEvent => ({
  state,
  at,
  run_id: "run-a",
  call_id: "call-a",
  tool_name: "run_workflow",
  source: "lifecycle",
  ...patch,
});

const start = (at = 100, patch: Partial<ExecutionEvent> = {}) =>
  trackExecution([], event("DELEGATING", at, patch));

describe("trackExecution", () => {
  it.each([
    ["ask", "Asking for clarification"],
    ["wait", "Waiting for a condition"],
    ["check", "Checking the result"],
  ])("labels %s without presenting it as a registered tool", (kind, title) => {
    const items = trackExecution(start(100), event("LISTENING", 200, {
      source: "skill", skill_run_id: "ask", outcome: "waiting",
      step_index: 1, step_count: 2, step_tool: kind, step_outcome: "waiting",
    }));
    expect(items[0].steps?.[0]).toMatchObject({ toolName: "", title, phase: "waiting" });
  });

  it("keeps a paused skill through outer completion and resumes by skill identity", () => {
    let items = start(100, { tool_name: "run_skill" });
    const skill = { source: "skill" as const, skill_run_id: "skill-pause", step_index: 1, step_count: 2, step_tool: "browser_click" };
    items = trackExecution(items, event("LISTENING", 200, { ...skill, outcome: "waiting", step_outcome: "waiting" }));
    const id = items[0].id;
    expect(items[0]).toMatchObject({ phase: "waiting", endedAt: null });
    items = trackExecution(items, event("DONE", 250));
    expect(items[0].phase).toBe("waiting");
    expect(expireExecution(items, 90_000)).toBe(items);
    expect(clearFinishedExecution(items)).toBe(items);
    items = trackExecution(items, event("RUNNING", 90_100, { ...skill, call_id: "answer-call", tool_name: "answer_skill", step_outcome: "running" }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id, phase: "running", startedAt: 100, skillRunId: "skill-pause" });
    items = trackExecution(items, event("DONE", 90_200, { ...skill, call_id: "answer-call", step_outcome: "done" }));
    expect(items[0]).toMatchObject({ phase: "finished", endedAt: 90_200 });
  });

  it("records actual numbered steps and explicit completion without finishing the run", () => {
    let items = start(100, { tool_name: "run_skill" });
    const skill = { source: "skill" as const, skill_run_id: "steps", step_count: 3 };
    items = trackExecution(items, event("RUNNING", 200, { ...skill, step_index: 1, step_tool: "desktop_move", step_outcome: "running" }));
    items = trackExecution(items, event("RUNNING", 500, { ...skill, step_index: 1, step_tool: "desktop_move", step_outcome: "done" }));
    expect(items[0].phase).toBe("running");
    expect(items[0].steps).toEqual([expect.objectContaining({ index: 1, toolName: "desktop_move", phase: "finished", startedAt: 200, endedAt: 500 })]);
    const completedSteps = items[0].steps;
    items = trackExecution(items, event("VERIFYING", 550, { ...skill, step_index: 1 }));
    expect(items[0].steps).toBe(completedSteps);
    items = trackExecution(items, event("VERIFYING", 600, { ...skill, step_index: 2, step_tool: "desktop_drag", step_outcome: "verifying" }));
    items = trackExecution(items, event("ERROR", 900, { ...skill, step_index: 2, step_tool: "desktop_drag", step_outcome: "error", outcome: "error" }));
    expect(items[0].steps).toEqual([
      expect.objectContaining({ index: 1, phase: "finished", endedAt: 500 }),
      expect.objectContaining({ index: 2, toolName: "desktop_drag", phase: "failed", endedAt: 900 }),
    ]);
    expect(items[0].phase).toBe("failed");
    expect(trackExecution(items, event("DONE", 950))).toBe(items);
  });

  it("marks running steps unavailable on stale status and waiting steps on disconnect", () => {
    const skill = { source: "skill" as const, skill_run_id: "stale", step_index: 1, step_count: 2, step_tool: "read_page" };
    const running = trackExecution(start(100), event("RUNNING", 200, { ...skill, step_outcome: "running" }));
    expect(expireExecution(running, 30_200)[0].steps?.[0].phase).toBe("unavailable");
    const waiting = trackExecution(running, event("LISTENING", 300, { ...skill, outcome: "waiting", step_outcome: "waiting" }));
    expect(interruptExecution(waiting, 400, "unavailable")[0]).toMatchObject({ phase: "unavailable", steps: [expect.objectContaining({ phase: "unavailable" })] });
  });

  it("bounds step history and does not retain arbitrary step tool text", () => {
    let items = start(0, { tool_name: "run_skill" });
    for (let index = 1; index <= 60; index += 1) {
      items = trackExecution(items, event("RUNNING", index * 100, { source: "skill", skill_run_id: "many", step_index: index, step_count: 60, step_tool: "private_exception_secret", step_outcome: "done" }));
    }
    expect(items[0].steps?.length).toBeLessThanOrEqual(32);
    expect(items[0].steps?.at(-1)?.index).toBe(60);
    expect(JSON.stringify(items)).not.toContain("private_exception_secret");
  });

  it("correlates skill steps with the existing call and preserves an uncertain terminal", () => {
    let items = start(100, { tool_name: "run_skill" });
    items = trackExecution(items, event("RUNNING", 200, {
      source: "skill", skill_run_id: "skill-7", skill_slug: "open-notes", skill_version: 3,
      step_index: 2, step_count: 4,
    }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ skillRunId: "skill-7", skillSlug: "open-notes", skillVersion: 3, progress: "Step 2 of 4" });
    items = trackExecution(items, event("ERROR", 300, { source: "skill", skill_run_id: "skill-7", outcome: "uncertain" }));
    expect(items[0].phase).toBe("uncertain");
    expect(trackExecution(items, event("DONE", 400))).toBe(items);
  });

  it("does not attach a different skill run to an established call", () => {
    let items = start(100, { tool_name: "run_skill" });
    items = trackExecution(items, event("RUNNING", 200, { source: "skill", skill_run_id: "first", step_index: 1, step_count: 3 }));
    expect(trackExecution(items, event("DONE", 300, { source: "skill", skill_run_id: "other" }))).toBe(items);
  });

  it("keeps concurrent calls to the same tool separate", () => {
    let items = start();
    items = trackExecution(items, event("DELEGATING", 110, { call_id: "call-b" }));
    items = trackExecution(items, event("VERIFYING", 120, { call_id: "call-a" }));
    items = trackExecution(items, event("DONE", 130, { call_id: "call-b" }));

    expect(items).toHaveLength(2);
    expect(new Set(items.map((item) => item.id)).size).toBe(2);
    expect(items.find((item) => item.callId === "call-a")).toMatchObject({
      phase: "verifying",
      startedAt: 100,
      updatedAt: 120,
      endedAt: null,
    });
    expect(items.find((item) => item.callId === "call-b")).toMatchObject({
      phase: "finished",
      startedAt: 110,
      updatedAt: 130,
      endedAt: 130,
    });
  });

  it("amends a known call with manual progress without creating duplicate rows", () => {
    let items = start();
    items = trackExecution(items, event("DELEGATING", 110, {
      source: "tool",
      detail: "desktop worker",
    }));
    items = trackExecution(items, event("RUNNING", 120, {
      source: "tool",
      detail: "step 2/3",
      tool_name: "open_app",
    }));

    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      toolName: "run_workflow",
      title: "Running a workflow",
      phase: "running",
      progress: "Step 2 of 3",
      updatedAt: 120,
    });
  });

  it("ignores manual events and terminal events for unknown calls", () => {
    for (const state of ["DELEGATING", "RUNNING", "VERIFYING", "DONE", "ERROR", "CANCELLED"]) {
      expect(trackExecution([], event(state, 100, { source: "tool" }))).toEqual([]);
    }
    for (const state of ["RUNNING", "VERIFYING", "DONE", "ERROR", "CANCELLED"]) {
      expect(trackExecution([], event(state, 100))).toEqual([]);
    }
  });

  it("does not let conversation states change a running call", () => {
    const items = start();
    for (const state of ["LISTENING", "SPEAKING", "GENERATING", "RECOGNIZING"]) {
      expect(trackExecution(items, event(state, 200))).toBe(items);
    }
  });

  it("keeps an internal failure when the SDK later reports generic completion", () => {
    let items = start();
    items = trackExecution(items, event("ERROR", 200, {
      source: "tool",
      detail: "secret exception at C:\\private\\credential.json",
    }));
    items = trackExecution(items, event("DONE", 250));

    expect(items[0]).toMatchObject({ phase: "failed", updatedAt: 200, endedAt: 200 });
    expect(JSON.stringify(items)).not.toContain("secret");
    expect(items[0].progress).toBeUndefined();
  });

  it("allows a late specific failure to correct generic completion", () => {
    let items = start();
    items = trackExecution(items, event("DONE", 200));
    items = trackExecution(items, event("ERROR", 250, { source: "tool" }));

    expect(items[0]).toMatchObject({ phase: "failed", updatedAt: 250, endedAt: 250 });
  });

  it("keeps cancellation when completion or another start arrives for the same call", () => {
    let items = start();
    items = trackExecution(items, event("CANCELLED", 200));
    const cancelled = items;
    items = trackExecution(items, event("DONE", 220));
    items = trackExecution(items, event("DELEGATING", 230));
    items = trackExecution(items, event("RUNNING", 240, { source: "tool" }));

    expect(items).toBe(cancelled);
    expect(items[0]).toMatchObject({ phase: "cancelled", endedAt: 200 });
  });

  it("does not reopen a finished call on a duplicate SDK start", () => {
    let items = start();
    items = trackExecution(items, event("DONE", 200));
    const completed = items;
    items = trackExecution(items, event("DELEGATING", 250));

    expect(items).toBe(completed);
    expect(items[0]).toMatchObject({ phase: "finished", startedAt: 100, endedAt: 200 });
  });

  it("interrupts the previous run when a new run starts", () => {
    let items = start();
    items = trackExecution(items, event("DELEGATING", 110, { call_id: "call-b" }));
    items = trackExecution(items, event("DELEGATING", 300, {
      run_id: "run-b",
      call_id: "call-a",
    }));

    expect(items.filter((item) => item.runId === "run-a")).toEqual([
      expect.objectContaining({ phase: "cancelled", endedAt: 300 }),
      expect.objectContaining({ phase: "cancelled", endedAt: 300 }),
    ]);
    expect(items.find((item) => item.runId === "run-b")).toMatchObject({
      phase: "running",
      startedAt: 300,
      endedAt: null,
    });
    expect(new Set(items.map((item) => item.id)).size).toBe(3);
    const restarted = items;
    expect(trackExecution(items, event("DONE", 400))).toBe(restarted);
  });

  it("keeps the eight most recently ended calls alongside all active calls", () => {
    let items: ExecutionActivity[] = [];
    for (let index = 0; index < 12; index += 1) {
      items = trackExecution(items, event("DELEGATING", index * 100, {
        call_id: `completed-${index}`,
      }));
      items = trackExecution(items, event("DONE", index * 100 + 50, {
        call_id: `completed-${index}`,
      }));
    }
    items = trackExecution(items, event("DELEGATING", 1_300, { call_id: "active" }));

    expect(items).toHaveLength(9);
    expect(items.filter((item) => item.phase === "finished").map((item) => item.callId))
      .toEqual(["completed-4", "completed-5", "completed-6", "completed-7", "completed-8", "completed-9", "completed-10", "completed-11"]);
    expect(items.find((item) => item.callId === "active")?.phase).toBe("running");
  });

  it("bounds concurrent activity without silently claiming an old call succeeded", () => {
    let items: ExecutionActivity[] = [];
    for (let index = 0; index < 40; index += 1) {
      items = trackExecution(items, event("DELEGATING", index * 100, {
        call_id: `active-${index}`,
      }));
    }

    expect(items.filter((item) => item.phase === "running")).toHaveLength(32);
    expect(items.filter((item) => item.phase === "unavailable")).toHaveLength(8);
    expect(items.filter((item) => item.phase === "finished")).toHaveLength(0);
    expect(items).toHaveLength(40);
  });

  it("uses only registered human labels and never retains an unknown tool name or detail", () => {
    const known = start(100, { tool_name: "read_page", detail: "private arguments" });
    const unknown = start(100, {
      tool_name: "malicious_tool_secret",
      detail: "private arguments C:\\secret",
    });

    expect(known[0]).toMatchObject({ toolName: "read_page", title: "Reading the page" });
    expect(unknown[0]).toMatchObject({ toolName: "", title: "Working on your request" });
    expect(JSON.stringify(unknown)).not.toContain("secret");
    expect(JSON.stringify(known)).not.toContain("private arguments");
  });

  it.each([
    ["step 2/5", "Step 2 of 5"],
    ["3/5 steps", "Step 3 of 5"],
    ["screen sent to Gemini", "Screen shared for review"],
    ["step 999/2", undefined],
    ["step 0/2", undefined],
    ["step 2/5: token=private", undefined],
    ["screen sent to Gemini: api-key", undefined],
    ["arbitrary output with secret", undefined],
  ])("maps safe progress from %s without passing arbitrary detail through", (detail, progress) => {
    const items = trackExecution(start(), event("RUNNING", 200, { source: "tool", detail }));
    expect(items[0].progress).toBe(progress);
  });

  it("uses request sent wording when an app launch request completes", () => {
    const items = trackExecution(start(100, { tool_name: "open_app" }), event("DONE", 200, {
      tool_name: "open_app",
      detail: "Opened Secret Application",
    }));

    expect(items[0]).toMatchObject({
      title: "Opening an app",
      phase: "finished",
      progress: "Request sent",
    });
    expect(JSON.stringify(items)).not.toContain("Secret Application");
  });

  it("ignores malformed correlations and invalid arrival timestamps", () => {
    for (const patch of [
      { run_id: "" },
      { call_id: " " },
      { at: Number.NaN },
      { at: Number.POSITIVE_INFINITY },
      { at: -1 },
    ]) {
      expect(trackExecution([], event("DELEGATING", 100, patch))).toEqual([]);
    }
  });

  it("does not let an out of order update shorten duration or reset progress", () => {
    let items = start(200);
    items = trackExecution(items, event("VERIFYING", 400, { detail: "step 2/3" }));
    const verifying = items;
    items = trackExecution(items, event("RUNNING", 300, { detail: "step 1/3" }));

    expect(items).toBe(verifying);
    expect(items[0]).toMatchObject({ phase: "verifying", updatedAt: 400, progress: "Step 2 of 3" });
  });

  it("does not mutate an input activity when applying a correlated update", () => {
    const items = start();
    const snapshot = JSON.stringify(items);
    trackExecution(items, event("DONE", 200));
    expect(JSON.stringify(items)).toBe(snapshot);
  });
});

describe("expireExecution", () => {
  it("marks a missing end event unavailable at thirty seconds, keeping the last real update time", () => {
    const items = start(1_000);
    expect(expireExecution(items, 30_999)).toBe(items);
    const expired = expireExecution(items, 31_000);

    expect(expired[0]).toMatchObject({
      phase: "unavailable",
      startedAt: 1_000,
      updatedAt: 1_000,
      endedAt: 31_000,
      progress: undefined,
    });
    expect(expireExecution(expired, 35_000)).toBe(expired);
    expect(items[0].phase).toBe("running");
  });

  it("lets a later correlated update recover unavailable activity", () => {
    let items = expireExecution(start(1_000), 31_000);
    items = trackExecution(items, event("VERIFYING", 32_000, {
      source: "tool",
      detail: "2/3 steps",
    }));
    expect(items[0]).toMatchObject({
      phase: "verifying",
      startedAt: 1_000,
      updatedAt: 32_000,
      endedAt: null,
      progress: "Step 2 of 3",
    });
    items = trackExecution(items, event("DONE", 33_000));
    expect(items[0]).toMatchObject({ phase: "finished", endedAt: 33_000 });
  });

  it("can resolve a late terminal result directly from unavailable", () => {
    const expired = expireExecution(start(1_000), 31_000);
    expect(trackExecution(expired, event("DONE", 33_000))[0]).toMatchObject({
      phase: "finished",
      endedAt: 33_000,
    });
    expect(trackExecution(expired, event("ERROR", 33_000))[0]).toMatchObject({
      phase: "failed",
      endedAt: 33_000,
    });
  });

  it("does not treat duplicate starts as recovery from unavailable", () => {
    const items = expireExecution(start(1_000), 31_000);
    expect(trackExecution(items, event("DELEGATING", 32_000))).toBe(items);
    expect(items[0].phase).toBe("unavailable");
  });
});

describe("interruptExecution", () => {
  it("cancels in flight work while preserving the previous terminal outcome", () => {
    let items = start(100, { call_id: "finished" });
    items = trackExecution(items, event("DONE", 200, { call_id: "finished" }));
    items = trackExecution(items, event("DELEGATING", 300, { call_id: "running" }));
    items = trackExecution(items, event("DELEGATING", 310, { call_id: "verifying" }));
    items = trackExecution(items, event("VERIFYING", 320, { call_id: "verifying" }));
    const interrupted = interruptExecution(items, 500);

    expect(interrupted.find((item) => item.callId === "finished")).toMatchObject({
      phase: "finished",
      endedAt: 200,
    });
    expect(interrupted.filter((item) => item.phase === "cancelled")).toHaveLength(2);
    expect(interrupted.find((item) => item.callId === "running")).toMatchObject({
      updatedAt: 500,
      endedAt: 500,
    });
    expect(items.find((item) => item.callId === "running")?.phase).toBe("running");
    expect(interruptExecution(interrupted, 600)).toBe(interrupted);
  });
});

describe("clearFinishedExecution", () => {
  it("removes terminal history while retaining active and verifying calls", () => {
    let items = start(100, { call_id: "finished" });
    items = trackExecution(items, event("DONE", 200, { call_id: "finished" }));
    items = trackExecution(items, event("DELEGATING", 300, { call_id: "failed" }));
    items = trackExecution(items, event("ERROR", 400, { call_id: "failed" }));
    items = trackExecution(items, event("DELEGATING", 500, { call_id: "stale" }));
    items = expireExecution(items, 30_500);
    items = trackExecution(items, event("DELEGATING", 31_000, { call_id: "active" }));
    items = trackExecution(items, event("DELEGATING", 31_100, { call_id: "verifying" }));
    items = trackExecution(items, event("VERIFYING", 31_200, { call_id: "verifying" }));

    const cleared = clearFinishedExecution(items);
    expect(cleared.map((item) => item.callId)).toEqual(["active", "verifying"]);
    expect(clearFinishedExecution(cleared)).toBe(cleared);
  });
});
