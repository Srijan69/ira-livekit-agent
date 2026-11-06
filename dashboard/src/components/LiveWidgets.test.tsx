import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UpcomingTasks } from "./UpcomingTasks";
import { WeatherCard } from "./WeatherCard";
import { QuickActions } from "./QuickActions";
import { CommandBar } from "./CommandBar";
import { DEFAULT_LOCATION } from "@/lib/weather";
import { QUICK_ACTIONS, SUGGESTIONS } from "@/lib/nav";

describe("live widgets", () => {
  it("separates loading, unavailable and an empty task list", () => {
    const props = {
      tasks: [],
      now: new Date(),
      onToggle: vi.fn(),
      onExpand: vi.fn(),
      onRetry: vi.fn(),
    };
    const { rerender } = render(<UpcomingTasks {...props} loading />);
    expect(screen.getByText(/loading tasks/i)).toBeInTheDocument();
    rerender(<UpcomingTasks {...props} error="Bridge unavailable" />);
    expect(screen.getByText("Bridge unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/nothing due/i)).toBeNull();
    rerender(<UpcomingTasks {...props} />);
    expect(screen.getByText(/nothing due/i)).toBeInTheDocument();
  });

  it("expands the task panel and allows completed tasks to be reopened", () => {
    const onExpand = vi.fn();
    const onToggle = vi.fn();
    render(
      <UpcomingTasks
        tasks={[
          {
            id: "1",
            title: "Done task",
            due_at: null,
            done: true,
            created_at: "2026-10-15T10:00:00Z",
          },
        ]}
        now={new Date()}
        onToggle={onToggle}
        onExpand={onExpand}
        expanded
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /show less/i }));
    expect(onExpand).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: /reopen/i }));
    expect(onToggle).toHaveBeenCalledWith("1");
  });

  it("keeps location controls reachable even without a weather reading", () => {
    render(
      <WeatherCard
        weather={null}
        location={DEFAULT_LOCATION}
        loading
        onLocationChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/loading weather/i)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /change weather location/i }),
    );
    expect(
      screen.getByRole("textbox", { name: /city name/i }),
    ).toBeInTheDocument();
  });

  it("starts every quick action with one focused question and keeps attachment promises out", () => {
    const onSend = vi.fn();
    render(<QuickActions onSend={onSend} />);
    for (const action of QUICK_ACTIONS) {
      fireEvent.click(screen.getByText(action.label).closest("button")!);
      expect(onSend).toHaveBeenLastCalledWith(
        expect.stringMatching(/one focused question/i),
      );
    }
    expect(QUICK_ACTIONS.find((action) => action.id === "analyze")?.sub).toBe(
      "Text & Data",
    );
    expect(QUICK_ACTIONS.find((action) => action.id === "create")?.sub).toBe(
      "Writing & Ideas",
    );
    expect(
      SUGGESTIONS.some((suggestion) =>
        /this PDF|this chart|reminder/i.test(suggestion.label),
      ),
    ).toBe(false);
  });

  it("keeps file attachment disabled", () => {
    render(
      <CommandBar onSend={vi.fn()} onToggleMic={vi.fn()} micEnabled={false} />,
    );
    expect(
      screen.getByRole("button", { name: "Attach a file" }),
    ).toBeDisabled();
  });
});
