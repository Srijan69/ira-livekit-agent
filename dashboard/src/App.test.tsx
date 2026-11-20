import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  clearFinishedExecution,
  type ExecutionActivity,
} from "./lib/execution";
import type { Session } from "./lib/session";
import type { IraEvent } from "@shared/types";

const computerEvents = vi.hoisted(() => ({ listeners: new Set<(event: IraEvent) => void>() }));

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
      subscribeEvents: (listener) => { computerEvents.listeners.add(listener); return () => { computerEvents.listeners.delete(listener); }; },
    };
  },
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("Home displays a validated screen observation inside execution status", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  await act(async () => { render(<App />); });
  act(() => { for (const listener of computerEvents.listeners) listener({ type: "computer.observation", run_id: "run", call_id: "active", tool_name: "capture_screen", sequence: 1, phase: "unavailable" }); });
  expect(within(screen.getByRole("region", { name: "Execution status" })).getByText("Observation unavailable")).toBeVisible();
});

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

it("opens profile and memory under System Controls and keeps profile drafts when switching tabs", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  await act(async () => { render(<App />); });
  fireEvent.click(screen.getByRole("button", { name: "System Controls" }));
  expect(screen.getByRole("heading", { name: "System Controls" })).toBeVisible();
  const profile = screen.getByRole("tab", { name: "Your Profile" });
  const memory = screen.getByRole("tab", { name: "Memory and Context" });
  expect(profile).toHaveAttribute("aria-selected", "true");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Draft name" } });
  fireEvent.keyDown(profile, { key: "End" });
  expect(memory).toHaveFocus();
  expect(screen.getByRole("heading", { name: "Memory and Context" })).toBeVisible();
  fireEvent.keyDown(memory, { key: "ArrowRight" });
  expect(profile).toHaveFocus();
  expect(screen.getByLabelText("Name")).toHaveValue("Draft name");
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: "Themes" })).toBeVisible();
  expect(screen.queryByRole("tab", { name: "Your Profile" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Memory and Context" })).not.toBeInTheDocument();
});
