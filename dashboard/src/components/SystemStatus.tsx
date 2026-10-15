import { Activity, Plug } from "lucide-react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { useEffect } from "react";

import type { SystemStats } from "@shared/types";

import { Panel, PanelHeader } from "./Panel";

const RADIUS = 25;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * Colour carries meaning rather than decoration.
 *
 * The reference image shows a green battery ring and cyan everywhere else,
 * which works at 87% and says nothing at 8%. These thresholds make the ring
 * worth glancing at.
 */
function loadColor(percent: number): string {
  if (percent >= 95) return "#EF4444";
  if (percent >= 85) return "#F59E0B";
  return "#22D3EE";
}

function batteryColor(percent: number, charging: boolean): string {
  if (charging) return "#22C55E";
  if (percent <= 15) return "#EF4444";
  if (percent <= 35) return "#F59E0B";
  return "#22C55E";
}

/**
 * One donut.
 *
 * The arc and the number are animated off a single spring, so they can never
 * disagree mid-transition. `stroke-dashoffset` does trigger paint, but it
 * fires on a 2-second stat update rather than per frame -- the one documented
 * exception to Section 3's transform-and-opacity rule.
 */
function Ring({
  label,
  percent,
  color,
  children,
}: {
  label: string;
  /** Null renders an idle ring with a dash, for "no data yet". */
  percent: number | null;
  color: string;
  /** Replaces the percentage, e.g. a plug icon on a desktop PC. */
  children?: React.ReactNode;
}) {
  const value = useMotionValue(0);
  const rounded = useTransform(value, (v) => Math.round(v));
  const offset = useTransform(
    value,
    (v) => CIRCUMFERENCE * (1 - Math.max(0, Math.min(100, v)) / 100),
  );

  useEffect(() => {
    if (percent === null) return;
    const controls = animate(value, percent, {
      type: "spring",
      stiffness: 120,
      damping: 20,
    });
    return () => controls.stop();
  }, [percent, value]);

  return (
    <li className="flex flex-col items-center gap-1.5">
      <div className="relative grid size-[62px] place-items-center">
        <svg viewBox="0 0 62 62" className="absolute size-full -rotate-90">
          <circle
            cx="31"
            cy="31"
            r={RADIUS}
            fill="none"
            stroke="rgb(255 255 255 / 0.07)"
            strokeWidth="4"
          />
          <motion.circle
            cx="31"
            cy="31"
            r={RADIUS}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            style={{
              strokeDashoffset: percent === null ? CIRCUMFERENCE : offset,
              filter: `drop-shadow(0 0 5px ${color}66)`,
            }}
          />
        </svg>
        <div className="flex flex-col items-center leading-none">
          <span className="label-caps !text-[7.5px] !tracking-[0.1em]">{label}</span>
          <span className="tnum mt-0.5 text-[14px] font-semibold text-text">
            {children ?? (
              percent === null ? (
                "--"
              ) : (
                <>
                  <motion.span>{rounded}</motion.span>%
                </>
              )
            )}
          </span>
        </div>
      </div>
    </li>
  );
}

export function SystemStatus({
  stats,
  delay = 0,
}: {
  stats: SystemStats | null;
  delay?: number;
}) {
  const battery = stats?.battery;

  return (
    <Panel delay={delay}>
      <PanelHeader
        icon={Activity}
        title="System Status"
        action={
          <span className="label-caps !text-[9px]">
            {stats ? (battery?.charging ? "Charging" : "Live") : "..."}
          </span>
        }
      />
      <ul className="flex items-start justify-around px-3 pt-0.5 pb-4">
        <Ring
          label="CPU"
          percent={stats?.cpu ?? null}
          color={loadColor(stats?.cpu ?? 0)}
        />
        <Ring
          label="RAM"
          percent={stats?.memory ?? null}
          color={loadColor(stats?.memory ?? 0)}
        />
        <Ring
          label="Storage"
          percent={stats?.storage ?? null}
          color={loadColor(stats?.storage ?? 0)}
        />
        {battery && !battery.hasBattery ? (
          // A desktop PC has no battery. A ring reading 0% would look like a
          // machine about to die, so show it's on mains instead.
          <Ring label="Power" percent={100} color="#22C55E">
            <Plug size={11} className="text-ok" />
          </Ring>
        ) : (
          <Ring
            label="Battery"
            percent={battery?.percent ?? null}
            color={batteryColor(battery?.percent ?? 0, battery?.charging ?? false)}
          />
        )}
      </ul>
    </Panel>
  );
}
