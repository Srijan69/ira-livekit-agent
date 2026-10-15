import { useEffect, useState } from "react";

/**
 * Whether animation should run at all.
 *
 * Two independent reasons to stop, combined because the components only care
 * about the answer:
 *
 *  - `prefers-reduced-motion`, which the OS sets and we must honour.
 *  - the window being hidden. Section 3's rule: an invisible dashboard
 *    spinning rings at 60fps is the reason these apps get a reputation for
 *    eating battery. Electron's main process stops polling stats on blur;
 *    this is the renderer's half of the same bargain.
 */
export function useAnimationEnabled(): boolean {
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

    const update = () => {
      setEnabled(!reduced.matches && document.visibilityState === "visible");
    };

    update();
    reduced.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      reduced.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  return enabled;
}
