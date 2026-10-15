import { formatClock, formatDay } from "@/lib/format";

/**
 * The header clock.
 *
 * `now` is a prop rather than internal state so a single tick in App drives
 * every time-dependent component. Nine components each running their own
 * interval is how a dashboard ends up with a visibly staggered clock.
 */
export function Clock({ now }: { now: Date }) {
  const { time, suffix } = formatClock(now);

  return (
    <div className="leading-none">
      <p className="mb-1.5 text-[11px] font-medium text-dim">{formatDay(now)}</p>
      <p className="flex items-baseline gap-1.5">
        <span className="tnum text-[30px] font-light tracking-tight text-text">
          {time}
        </span>
        <span className="text-[13px] font-medium text-dim">{suffix}</span>
      </p>
    </div>
  );
}
