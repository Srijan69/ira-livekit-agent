import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY } from "@/lib/themes";
import { useTheme } from "./useTheme";

describe("useTheme", () => {
  const originalIra = Object.getOwnPropertyDescriptor(window, "ira");
  const restart = vi.fn(async () => {});

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    restart.mockReset();
    restart.mockResolvedValue(undefined);
    Object.defineProperty(window, "ira", {
      configurable: true,
      value: { restart },
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    localStorage.clear();
    if (originalIra) Object.defineProperty(window, "ira", originalIra);
    else delete window.ira;
  });

  it("starts with the saved theme without requesting a restart", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "gold");

    const { result } = renderHook(() => useTheme());

    expect(result.current.activeTheme).toBe("gold");
    expect(result.current.pendingTheme).toBeNull();
    expect(result.current.error).toBeNull();
    expect(restart).not.toHaveBeenCalled();
  });

  it("ignores selecting the active theme", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "red");
    const { result } = renderHook(() => useTheme());

    await act(async () => {
      await result.current.selectTheme("red");
      await vi.runAllTimersAsync();
    });

    expect(result.current.activeTheme).toBe("red");
    expect(result.current.pendingTheme).toBeNull();
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("red");
    expect(restart).not.toHaveBeenCalled();
  });

  it("accepts only the first rapid selection while a restart is in progress", async () => {
    let finishRestart!: () => void;
    restart.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishRestart = resolve;
        }),
    );
    const { result } = renderHook(() => useTheme());

    // One React event batch also covers a second click before rerendering.
    act(() => {
      void result.current.selectTheme("green");
      void result.current.selectTheme("green");
      void result.current.selectTheme("pink");
    });
    expect(result.current.pendingTheme).toBe("green");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("green");

    await act(async () => {
      await vi.runAllTimersAsync();
    });
    expect(restart).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.selectTheme("silver");
    });
    expect(result.current.pendingTheme).toBe("green");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("green");
    expect(restart).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishRestart();
    });
  });

  it("persists the choice before calling the desktop restart boundary", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "silver");
    let savedAtRestart: string | null = null;
    restart.mockImplementationOnce(async () => {
      savedAtRestart = localStorage.getItem(THEME_STORAGE_KEY);
    });
    const { result } = renderHook(() => useTheme());

    act(() => {
      void result.current.selectTheme("gold");
    });

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("gold");
    expect(result.current.activeTheme).toBe("silver");
    expect(result.current.pendingTheme).toBe("gold");
    expect(restart).not.toHaveBeenCalled();

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(restart).toHaveBeenCalledTimes(1);
    expect(savedAtRestart).toBe("gold");
  });

  it("keeps the restart screen pending after desktop acceptance until the app exits", async () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      void result.current.selectTheme("pink");
    });
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(restart).toHaveBeenCalledTimes(1);
    expect(result.current.activeTheme).toBe("blue");
    expect(result.current.pendingTheme).toBe("pink");
    expect(result.current.error).toBeNull();

    await act(async () => {
      await result.current.selectTheme("green");
      await vi.runAllTimersAsync();
    });

    expect(restart).toHaveBeenCalledTimes(1);
    expect(result.current.pendingTheme).toBe("pink");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("pink");
  });

  it.each([
    { saved: "gold", active: "gold" },
    { saved: null, active: "blue" },
  ])(
    "rolls back a rejected restart and permits retry with saved preference $saved",
    async ({ saved, active }) => {
      if (saved !== null) localStorage.setItem(THEME_STORAGE_KEY, saved);
      restart.mockRejectedValueOnce(new Error("Desktop restart unavailable"));
      const { result } = renderHook(() => useTheme());

      act(() => {
        void result.current.selectTheme("pink");
      });
      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(result.current.activeTheme).toBe(active);
      expect(result.current.pendingTheme).toBeNull();
      expect(result.current.error).toMatch(/try again/i);
      expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe(saved);

      act(() => {
        void result.current.selectTheme("pink");
      });

      expect(result.current.error).toBeNull();
      expect(result.current.pendingTheme).toBe("pink");
      expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("pink");

      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(restart).toHaveBeenCalledTimes(2);
      expect(result.current.activeTheme).toBe(active);
      expect(result.current.pendingTheme).toBe("pink");
      expect(result.current.error).toBeNull();
    },
  );
});
