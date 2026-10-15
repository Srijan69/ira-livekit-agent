import { useEffect, useState } from "react";

/**
 * A ticking clock.
 *
 * The interval is a parameter because the header and the task list want
 * different rates: the clock needs every second, while due labels only change
 * on the minute. Re-rendering the task list 60 times a minute to recompute
 * strings that didn't change is exactly the kind of waste that shows up as a
 * dashboard warming your laptop.
 *
 * Ticking stops while the document is hidden and resyncs on return, so a
 * minimized window costs nothing and doesn't show a stale time when restored.
 */
export function useClock(intervalMs = 1_000): Date {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;

    const start = () => {
      if (timer !== null) return;
      setNow(new Date());
      timer = setInterval(() => setNow(new Date()), intervalMs);
    };

    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };

    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);

  return now;
}
