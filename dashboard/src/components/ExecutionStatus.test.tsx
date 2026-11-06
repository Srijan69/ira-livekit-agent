import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ExecutionActivity } from "@/lib/execution";
import type { OrbState } from "@/lib/orbState";
import { ExecutionStatus } from "./ExecutionStatus";

const activity = (overrides: Partial<ExecutionActivity> = {}): ExecutionActivity => ({
  id: "action-1",
  runId: "run-1",
  callId: "call-1",
  toolName: "launch_native_app",
  title: "Opening Calculator",
  phase: "running",
  startedAt: 1_000,
  updatedAt: 1_000,
  endedAt: null,
  ...overrides,
});

const base = {
  activities: [] as ExecutionActivity[],
  state: "listening" as OrbState,
  now: 9_000,
  onClear: vi.fn(),
  animate: false,
};

describe("execution status", () => {
  it("shows registered tool names and a numbered step timeline with honest outcomes", () => {
    render(<ExecutionStatus {...base} activities={[activity({
      toolName: "run_skill", phase: "waiting", skillRunId: "skill-1",
      steps: [
        { index: 1, toolName: "desktop_move", title: "Moving the pointer", phase: "finished", startedAt: 1_000, updatedAt: 3_000, endedAt: 3_000 },
        { index: 2, toolName: "desktop_drag", title: "Dragging on the desktop", phase: "waiting", startedAt: 3_000, updatedAt: 4_000, endedAt: null },
      ],
    })]} />);
    expect(screen.getByText("run_skill")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("desktop_drag");
    const steps = screen.getByRole("list", { name: "Skill steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(2);
    expect(within(steps).getByText("1")).toBeVisible();
    expect(within(steps).getByText("2")).toBeVisible();
    expect(within(steps).getByText("desktop_move")).toBeVisible();
    expect(within(steps).getByText("desktop_drag")).toBeVisible();
    expect(within(steps).getByText("Finished")).toBeVisible();
    expect(within(steps).getByText("Waiting for answer")).toBeVisible();
    expect(within(steps).getByText("2s")).toBeVisible();
    expect(screen.getByRole("button", { name: "Clear finished" })).toBeDisabled();
  });

  it("shows the real connection state before any action has started", () => {
    const { rerender } = render(<ExecutionStatus {...base} state="offline" />);
    expect(screen.getByRole("heading", { name: "Execution status" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Offline");
    expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear finished" })).toBeDisabled();

    rerender(<ExecutionStatus {...base} state="connecting" />);
    expect(screen.getByRole("status")).toHaveTextContent("Connecting");
    expect(screen.queryByText("LIVE")).not.toBeInTheDocument();

    rerender(<ExecutionStatus {...base} />);
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("Actions appear here as IRA works")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it.each([
    ["recognizing", "Listening"],
    ["generating", "Thinking"],
    ["speaking", "Responding"],
    ["executing", "Working"],
  ] as const)("shows %s without inventing action history", (state, label) => {
    render(<ExecutionStatus {...base} state={state} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("updates one action in place and stops its duration when it finishes", () => {
    const running = activity({ progress: "Waiting for the app window" });
    const { rerender } = render(<ExecutionStatus {...base} activities={[running]} />);
    let row = screen.getByRole("listitem");
    expect(within(row).getByText("Opening Calculator")).toBeInTheDocument();
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).getByText("Waiting for the app window")).toBeInTheDocument();
    expect(within(row).getByText("8s")).toBeInTheDocument();
    expect(screen.queryByText("launch_native_app")).not.toBeInTheDocument();

    rerender(
      <ExecutionStatus
        {...base}
        now={20_000}
        activities={[activity({ phase: "finished", updatedAt: 4_000, endedAt: 4_000 })]}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    row = screen.getByRole("listitem");
    expect(within(row).getByText("Finished")).toBeInTheDocument();
    expect(within(row).getByText("3s")).toBeInTheDocument();
    expect(within(row).queryByText("Running")).not.toBeInTheDocument();
  });

  it("puts active work before the recent history and shows each outcome distinctly", () => {
    render(
      <ExecutionStatus
        {...base}
        activities={[
          activity({ id: "finished", title: "Opened Calculator", phase: "finished", endedAt: 3_000, updatedAt: 3_000 }),
          activity({ id: "failed", title: "Opening Notes", phase: "failed", endedAt: 6_000, updatedAt: 6_000 }),
          activity({ id: "cancelled", title: "Opening Browser", phase: "cancelled", endedAt: 5_000, updatedAt: 5_000 }),
          activity({ id: "unavailable", title: "Checking the window", phase: "unavailable", endedAt: 4_000, updatedAt: 4_000 }),
          activity({ id: "verifying", title: "Checking Calculator", phase: "verifying", updatedAt: 2_000 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => within(row).getByRole("heading").textContent)).toEqual([
      "Checking Calculator", "Opening Notes", "Opening Browser", "Checking the window", "Opened Calculator",
    ]);
    for (const label of ["Verifying", "Failed", "Cancelled", "Unavailable", "Finished"]) {
      expect(screen.getByRole("list")).toHaveTextContent(label);
    }
  });

  it("clears only finished history through the supplied action", () => {
    const onClear = vi.fn();
    const { rerender } = render(<ExecutionStatus {...base} onClear={onClear} activities={[activity()]} />);
    expect(screen.getByRole("button", { name: "Clear finished" })).toBeDisabled();
    rerender(<ExecutionStatus {...base} onClear={onClear} activities={[activity({ phase: "cancelled", endedAt: 4_000 })]} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear finished" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("collapses history accessibly while keeping the current status visible", () => {
    render(<ExecutionStatus {...base} activities={[activity()]} />);
    const toggle = screen.getByRole("button", { name: /execution status/i });
    const historyId = toggle.getAttribute("aria-controls")!;
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(historyId)).toBeVisible();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(historyId)).not.toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Opening Calculator");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("listitem")).toBeVisible();
  });
});
