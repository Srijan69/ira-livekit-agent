import { Clock } from "./Clock";
import { ConnectionBadge, type ConnectionState } from "./ConnectionBadge";
import { Greeting } from "./Greeting";
import { WindowControls } from "./WindowControls";

/**
 * The IRA mark: concentric arcs around a lit core.
 *
 * Drawn rather than imported so it inherits the theme's cyan and stays sharp
 * at any scale. Abstract on purpose -- see the note in Greeting about not
 * borrowing a character.
 */
function Logo() {
  return (
    <svg viewBox="0 0 48 48" className="size-11" aria-hidden>
      <defs>
        <radialGradient id="logo-core">
          <stop offset="0%" stopColor="#CFF8FF" />
          <stop offset="55%" stopColor="#22D3EE" />
          <stop offset="100%" stopColor="#2E7BFF" />
        </radialGradient>
      </defs>
      <circle cx="24" cy="24" r="21" fill="none" stroke="#2E7BFF" strokeWidth="1" opacity="0.35" />
      {/* Four arcs with gaps, matching the segmented ring in the reference. */}
      {[0, 90, 180, 270].map((rotation) => (
        <path
          key={rotation}
          d="M 24 7 A 17 17 0 0 1 41 24"
          fill="none"
          stroke="#22D3EE"
          strokeWidth="2.5"
          strokeLinecap="round"
          opacity="0.9"
          transform={`rotate(${rotation} 24 24)`}
          pathLength={1}
          strokeDasharray="0.78 0.22"
        />
      ))}
      <circle cx="24" cy="24" r="10" fill="none" stroke="#22D3EE" strokeWidth="1.5" opacity="0.5" />
      <circle cx="24" cy="24" r="6" fill="url(#logo-core)" />
    </svg>
  );
}

/**
 * The header. The whole bar is a drag region for the frameless window, so
 * anything interactive inside it carries `.no-drag`.
 */
export function TopBar({
  now,
  connection,
  micEnabled,
  onToggleMic,
}: {
  now: Date;
  connection: ConnectionState;
  micEnabled: boolean;
  onToggleMic: () => void;
}) {
  return (
    <header className="drag-region relative flex shrink-0 items-start justify-between gap-6 px-6 pt-5 pb-4">
      <div className="flex items-center gap-7">
        <div className="flex items-center gap-2.5">
          <Logo />
          <div className="leading-none">
            <p className="text-[26px] font-bold tracking-[0.02em] text-text">IRA</p>
            <p className="label-caps mt-1 !text-[8px]">Your AI Assistant</p>
          </div>
        </div>
        <Clock now={now} />
      </div>

      {/* Centred independently of the flanking columns, so an unusually long
          greeting can't shove it off-centre. */}
      <div className="pointer-events-none absolute inset-x-0 top-6 flex justify-center">
        <Greeting now={now} />
      </div>

      <div className="flex items-center gap-2.5">
        <ConnectionBadge state={connection} />
        <WindowControls micEnabled={micEnabled} onToggleMic={onToggleMic} />
      </div>
    </header>
  );
}
