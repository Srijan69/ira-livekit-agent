import { Mic, MicOff, Minus, X } from "lucide-react";

/**
 * Mic toggle plus the frameless window's own controls.
 *
 * Every button is `.no-drag`: the surrounding header is a drag region, and
 * without opting out these would move the window instead of being clickable.
 *
 * `window.ira` is optional throughout -- under Vitest there is no preload
 * bridge, and a dashboard that throws in a plain browser is harder to work on
 * than one whose buttons quietly do nothing.
 */
export function WindowControls({
  micEnabled,
  onToggleMic,
}: {
  micEnabled: boolean;
  onToggleMic: () => void;
}) {
  return (
    <div className="no-drag flex items-center gap-2">
      <button
        type="button"
        onClick={onToggleMic}
        aria-label={micEnabled ? "Mute microphone" : "Unmute microphone"}
        aria-pressed={micEnabled}
        className="panel grid size-9 place-items-center rounded-full text-text transition-colors hover:bg-white/10"
        style={
          micEnabled
            ? { borderColor: "rgb(var(--accent-rgb) / 0.35)", color: "var(--color-cyan)" }
            : undefined
        }
      >
        {micEnabled ? (
          <Mic size={15} strokeWidth={1.75} />
        ) : (
          <MicOff size={15} strokeWidth={1.75} />
        )}
      </button>

      <button
        type="button"
        onClick={() => void window.ira?.window.minimize()}
        aria-label="Minimize"
        className="panel grid size-9 place-items-center rounded-full text-dim transition-colors hover:bg-white/10 hover:text-text"
      >
        <Minus size={15} strokeWidth={2} />
      </button>

      <button
        type="button"
        onClick={() => void window.ira?.window.close()}
        aria-label="Close"
        className="panel grid size-9 place-items-center rounded-full text-dim transition-colors hover:bg-bad/20 hover:text-text"
      >
        <X size={15} strokeWidth={2} />
      </button>
    </div>
  );
}
