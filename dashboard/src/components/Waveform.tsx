import { useEffect, useRef } from "react";

const BAR_COUNT = 48;
const BAR_WIDTH = 2.5;
const BAR_GAP = 3.5;

/**
 * The audio waveform under the orb.
 *
 * Canvas rather than SVG, and driven by a `getLevel` callback rather than a
 * prop, because this is the one thing on screen that genuinely updates every
 * frame. Pushing 48 numbers through React state at 60fps would re-render the
 * whole stage 60 times a second; here, nothing re-renders at all -- the loop
 * reads the current level and draws.
 *
 * `getLevel` is also the seam for sub-project B: it returns synthetic motion
 * today and real track amplitude once there's a session, with no change here.
 */
export function Waveform({
  getLevel,
  active,
  width = 320,
  height = 44,
  color = "var(--color-cyan)",
}: {
  /** Current loudness, 0..1. Called once per frame. */
  getLevel: () => number;
  /** False parks the bars flat and stops the loop. */
  active: boolean;
  width?: number;
  height?: number;
  color?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Bar heights persist across frames so they decay smoothly instead of
  // flickering independently each frame.
  const bars = useRef<number[]>(new Array(BAR_COUNT).fill(0));
  const getLevelRef = useRef(getLevel);
  getLevelRef.current = getLevel;

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    context.scale(dpr, dpr);
    // Canvas cannot resolve CSS variables in fillStyle. Resolve the canvas's
    // theme color once; the selected theme is applied before this view mounts.
    const resolvedColor = getComputedStyle(canvas).color;

    const centre = height / 2;
    const totalWidth = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP;
    const startX = (width - totalWidth) / 2;

    const draw = () => {
      const level = active ? Math.max(0, Math.min(1, getLevelRef.current())) : 0;
      context.clearRect(0, 0, width, height);

      for (let i = 0; i < BAR_COUNT; i += 1) {
        // Taper toward the ends so the waveform reads as one shape rather
        // than a block of bars.
        const fromCentre = Math.abs(i - (BAR_COUNT - 1) / 2) / ((BAR_COUNT - 1) / 2);
        const envelope = Math.cos(fromCentre * 1.35) ** 2;

        // Two offset sines give an irregular, voice-like profile; a single
        // one looks like a test pattern.
        const phase = performance.now() / 180;
        const wobble =
          0.55 + 0.45 * Math.sin(phase + i * 0.55) * Math.sin(phase * 0.6 + i * 0.21);

        const target = active ? level * envelope * wobble : 0;
        // Asymmetric smoothing: snap up, fall off slowly -- how a level meter
        // behaves, and what keeps speech from looking like a sine sweep.
        const current = bars.current[i] ?? 0;
        const rate = target > current ? 0.45 : 0.12;
        const next = current + (target - current) * rate;
        bars.current[i] = next;

        const barHeight = Math.max(1.5, next * (height - 4));
        const x = startX + i * (BAR_WIDTH + BAR_GAP);

        context.globalAlpha = 0.25 + next * 0.75;
        context.fillStyle = resolvedColor;
        context.beginPath();
        context.roundRect(
          x,
          centre - barHeight / 2,
          BAR_WIDTH,
          barHeight,
          BAR_WIDTH / 2,
        );
        context.fill();
      }
      context.globalAlpha = 1;
    };

    if (!active) {
      // One final frame so the bars render flat rather than vanishing.
      bars.current.fill(0);
      draw();
      return;
    }

    let frame = requestAnimationFrame(function loop() {
      draw();
      frame = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(frame);
  }, [active, width, height, color]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      style={{ width, height, color }}
      className="block"
    />
  );
}
