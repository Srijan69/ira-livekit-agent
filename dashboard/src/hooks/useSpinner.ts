import { useEffect, useRef } from "react";
import { useMotionValue, type MotionValue } from "motion/react";

/**
 * A continuously rotating value whose speed can change without snapping.
 *
 * The obvious approach -- `animate={{ rotate: 360 }}` with a `duration` keyed
 * off state -- restarts the animation from 0 whenever the duration changes, so
 * every state transition jumps the rings back to their start. Section 3
 * promised rotation that "spins up", which means the speed has to be
 * interpolated rather than the animation replaced.
 *
 * So: one frame loop, a persistent angle, and a speed eased toward its target.
 * The loop is torn down entirely when `enabled` is false -- Motion's
 * `useAnimationFrame` keeps running its callback regardless, which would make
 * the idle-CPU target unreachable behind a hidden window.
 */
export function useSpinner(
  targetDegreesPerSecond: number,
  enabled: boolean,
): MotionValue<number> {
  const rotation = useMotionValue(0);
  const speed = useRef(targetDegreesPerSecond);
  const target = useRef(targetDegreesPerSecond);

  // Read fresh every render, so a speed change never restarts the loop below.
  target.current = targetDegreesPerSecond;

  useEffect(() => {
    if (!enabled) return;

    let frame = 0;
    let last = performance.now();

    const tick = (time: number) => {
      // Clamped: returning from a hidden window hands us one enormous delta,
      // which would otherwise fling the rings through several revolutions.
      const delta = Math.min(time - last, 50);
      last = time;

      // Exponential approach, framerate-independent. ~400ms to close the gap,
      // which matches the transition timing in Section 3.
      speed.current += (target.current - speed.current) * Math.min(1, delta / 400);
      rotation.set((rotation.get() + (speed.current * delta) / 1000) % 360);

      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [enabled, rotation]);

  return rotation;
}
