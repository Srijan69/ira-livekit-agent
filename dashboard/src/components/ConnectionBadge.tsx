import { motion } from "motion/react";

export type ConnectionState = "disconnected" | "connecting" | "connected";

const COPY: Record<ConnectionState, { label: string; color: string }> = {
  connected: { label: "Online", color: "#22C55E" },
  connecting: { label: "Connecting", color: "#F59E0B" },
  disconnected: { label: "Offline", color: "#64748B" },
};

/**
 * The Online pill.
 *
 * Reports whether IRA is actually in the room, not whether the bridge
 * answered. Those differ, and the honest signal is the one the user cares
 * about: can she hear me.
 */
export function ConnectionBadge({ state }: { state: ConnectionState }) {
  const { label, color } = COPY[state];

  return (
    <div className="panel no-drag flex items-center gap-2 rounded-full px-3 py-1.5">
      <span className="relative grid size-2 place-items-center">
        <span
          className="absolute size-2 rounded-full"
          style={{ background: color }}
        />
        {state === "connecting" && (
          <motion.span
            className="absolute size-2 rounded-full"
            style={{ background: color }}
            animate={{ scale: [1, 2.4], opacity: [0.6, 0] }}
            transition={{ duration: 1.4, repeat: Infinity, ease: "easeOut" }}
          />
        )}
      </span>
      <span className="text-[12px] font-medium text-text">{label}</span>
    </div>
  );
}
