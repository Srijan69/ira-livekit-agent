import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  clearFinishedExecution,
  type ExecutionActivity,
} from "./lib/execution";
import type { Session } from "./lib/session";

vi.mock("./hooks/useSession", () => ({
  useSession: (): Session => {
    const [activities, setActivities] = useState<ExecutionActivity[]>([
      {
        id: "running",
        runId: "run",
        callId: "active",
        toolName: "scroll_mouse",
        title: "Scrolling on the desktop",
        phase: "running",
        startedAt: 1_000,
        updatedAt: 2_000,
        endedAt: null,
      },
      {
        id: "done",
        runId: "run",
        callId: "done",
        toolName: "read_page",
        title: "Reading the page",
        phase: "finished",
        startedAt: 500,
        updatedAt: 1_000,
        endedAt: 1_000,
      },
    ]);
    return {
      activities,
      clearExecutionHistory: () => setActivities(clearFinishedExecution),
      connection: "connected",
      orbState: "executing",
      cues: [],
      micEnabled: false,
      toggleMic: () => {},
      getLevel: () => 0,
      send: () => {},
      error: null,
      subscribeEvents: () => () => {},
    };
  },
}));

afterEach(() => vi.unstubAllGlobals());

it("Home renders execution beside the orb and clears finished actions through the session", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  await act(async () => {
    render(<App />);
  });
  const panel = screen.getByRole("region", { name: "Execution status" });
  expect(within(panel).getByText("scroll_mouse")).toBeVisible();
  expect(within(panel).getByText("read_page")).toBeVisible();
  fireEvent.click(
    within(panel).getByRole("button", { name: "Clear finished" }),
  );
  expect(within(panel).queryByText("read_page")).not.toBeInTheDocument();
  expect(within(panel).getByText("scroll_mouse")).toBeVisible();
});
