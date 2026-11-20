import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EMPTY_COMPUTER_USE, trackComputerUse } from "@/lib/computerUse";
import { parseComputerObservation } from "@shared/computerUse";
import { ComputerUsePreview } from "./ComputerUsePreview";

const at = Date.parse("2026-11-07T12:00:00Z");
const data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=";
const observed = parseComputerObservation({ type: "computer.observation", run_id: "run", call_id: "call", tool_name: "desktop_click", sequence: 1, phase: "before", observation_id: "screen", captured_at: "2026-11-07T12:00:00Z", image_size: [1, 1], screenshot_data_url: data, targets: [{ id: "control", name: "Observed control", role: "button", image_bounds: [0, 0, 1, 1] }], action: { action: "click", observation_id: "screen", image_points: [[0, 0]] } })!;
const state = trackComputerUse(EMPTY_COMPUTER_USE, observed, at);
const base = { model: state, connected: true, now: at + 1_000, animate: false, activities: [] };

describe("computer screen preview", () => {
  it("shows observed targets and action markers on the actual frame", () => {
    const { container } = render(<ComputerUsePreview {...base} />);
    expect(screen.getByRole("img", { name: /before action/i })).toHaveAttribute("src", data);
    expect(container.querySelector('[data-target-id="control"]')).toBeInTheDocument();
    expect(container.querySelector('[data-action-marker="click"]')).toBeInTheDocument();
    expect(screen.getByText("Clicking")).toBeVisible();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });
  it("labels stale observations and never presents them as a live screen", () => {
    render(<ComputerUsePreview {...base} now={at + 60_000} />);
    expect(screen.getByText("Last observed")).toBeVisible();
    expect(screen.getByText(/Screen may have changed/)).toBeVisible();
    expect(screen.queryByText("Fresh capture")).not.toBeInTheDocument();
  });
  it("removes screen evidence and offers honest empty states while disconnected", () => {
    const { rerender } = render(<ComputerUsePreview {...base} connected={false} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/Connect to see/)).toBeVisible();
    rerender(<ComputerUsePreview {...base} model={EMPTY_COMPUTER_USE} />);
    expect(screen.getByText(/No screen observation yet/)).toBeVisible();
  });
  it("opens an enlarged keyboard-accessible image and returns focus when closed", () => {
    render(<ComputerUsePreview {...base} />);
    const expand = screen.getByRole("button", { name: "Enlarge observed screen" });
    expand.focus();
    fireEvent.click(expand);
    expect(screen.getByRole("dialog", { name: "Observed screen" })).toBeVisible();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(expand).toHaveFocus();
  });
  it("hides overlays and reports when the image cannot be decoded", () => {
    const { container } = render(<ComputerUsePreview {...base} />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText("Screen image unavailable")).toBeVisible();
    expect(container.querySelector('[data-action-marker]')).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enlarge observed screen" })).not.toBeInTheDocument();
  });
  it("does not reopen the enlarged view when a disconnected session returns", () => {
    const { rerender } = render(<ComputerUsePreview {...base} />);
    fireEvent.click(screen.getByRole("button", { name: "Enlarge observed screen" }));
    rerender(<ComputerUsePreview {...base} connected={false} />);
    rerender(<ComputerUsePreview {...base} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("outlines the action target even when the frame's target list omits it", () => {
    const actionTarget = { id: "selected", name: "Selected control", role: "button", image_bounds: [0, 0, 1, 1] as [number, number, number, number] };
    const model = trackComputerUse(EMPTY_COMPUTER_USE, { ...observed, targets: [], action: { ...observed.action!, target: actionTarget } }, at);
    const { container } = render(<ComputerUsePreview {...base} model={model} />);
    expect(container.querySelector('[data-target-id="selected"] .computer-target')).toBeInTheDocument();
  });
});
