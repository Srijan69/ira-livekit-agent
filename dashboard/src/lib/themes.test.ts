import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyTheme,
  readThemePreference,
  restartWithTheme,
  THEME_STORAGE_KEY,
} from "./themes";

describe("theme preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("style");
    delete document.documentElement.dataset.theme;
  });

  it("restores a saved theme and falls back to blue for an unknown preference", () => {
    expect(readThemePreference()).toBe("blue");
    localStorage.setItem(THEME_STORAGE_KEY, "green");
    expect(readThemePreference()).toBe("green");
    localStorage.setItem(THEME_STORAGE_KEY, "removed-theme");
    expect(readThemePreference()).toBe("blue");
  });

  it("still starts when browser storage is unavailable", () => {
    const storage = {
      getItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readThemePreference(storage)).toBe("blue");
  });

  it("applies the saved green palette before the dashboard renders", () => {
    applyTheme("green");
    expect(document.documentElement.dataset.theme).toBe("green");
    expect(
      document.documentElement.style.getPropertyValue("--color-cyan"),
    ).toBe("#49f787");
    expect(document.documentElement.style.getPropertyValue("--orb-ring")).toBe(
      "#49f787",
    );
  });

  it("saves the new choice before restarting, without changing the current screen palette", async () => {
    applyTheme("blue");
    const restart = vi.fn(async () => {
      expect(readThemePreference()).toBe("green");
      expect(document.documentElement.dataset.theme).toBe("blue");
    });
    await restartWithTheme("green", restart);
    expect(restart).toHaveBeenCalledOnce();
    expect(readThemePreference()).toBe("green");
  });

  it("keeps the previous choice if the desktop rejects the restart", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "gold");
    await expect(
      restartWithTheme("pink", async () => {
        throw new Error("restart unavailable");
      }),
    ).rejects.toThrow("restart unavailable");
    expect(readThemePreference()).toBe("gold");
  });

  it("does not shut down if the new preference cannot be saved", async () => {
    const restart = vi.fn();
    const storage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("storage full");
      },
      removeItem: vi.fn(),
    };
    await expect(restartWithTheme("red", restart, storage)).rejects.toThrow();
    expect(restart).not.toHaveBeenCalled();
  });
});
