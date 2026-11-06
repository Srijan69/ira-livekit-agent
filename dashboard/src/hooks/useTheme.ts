import { useRef, useState } from "react";

import {
  readThemePreference,
  restartWithTheme,
  type ThemeId,
} from "@/lib/themes";

export function useTheme() {
  const [activeTheme] = useState(readThemePreference);
  const [pendingTheme, setPendingTheme] = useState<ThemeId | null>(null);
  const [error, setError] = useState<string | null>(null);
  const restarting = useRef(false);

  async function selectTheme(id: ThemeId) {
    if (id === activeTheme || restarting.current) return;
    restarting.current = true;
    setPendingTheme(id);
    setError(null);

    try {
      await restartWithTheme(id, async () => {
        // Allow the restart screen to paint before the native window closes.
        await new Promise<void>((resolve) => window.setTimeout(resolve, 650));
        if (window.ira) await window.ira.restart();
        else window.location.reload();
      });
    } catch {
      restarting.current = false;
      setPendingTheme(null);
      setError(
        "Your theme could not be saved or IRA could not restart. Please try again.",
      );
    }
  }

  return { activeTheme, pendingTheme, error, selectTheme };
}
