import { AlertTriangle, RotateCw, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import type { ProcessStatus } from "@shared/types";

/**
 * The thing that stops a failure from looking like a hang.
 *
 * Without this, every problem in Section 5's table renders identically: an orb
 * saying CONNECTING forever. A missing GOOGLE_API_KEY, a crashed worker and an
 * unreachable LiveKit all need different fixes, so each one gets named, with
 * the action that addresses it.
 */
export function StatusBanner({ error }: { error: string | null }) {
  const [processes, setProcesses] = useState<ProcessStatus[]>([]);
  const [dismissed, setDismissed] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    const bridge = window.ira;
    if (!bridge) return;

    void bridge.agent.status().then(({ agent, bridge: bridgeStatus }) => {
      setProcesses([agent, bridgeStatus].filter(Boolean) as ProcessStatus[]);
    });

    return bridge.agent.onStatus((status) => {
      setProcesses((current) => [
        ...current.filter((entry) => entry.name !== status.name),
        status,
      ]);
      // A new problem un-dismisses: having waved away one failure shouldn't
      // hide the next.
      if (status.state === "failed") setDismissed(false);
    });
  }, []);

  const failed = processes.find((entry) => entry.state === "failed");
  const message = failed
    ? `${failed.name === "agent" ? "IRA" : "The bridge"} stopped: ${failed.reason ?? "unknown reason"}`
    : error;

  const show = Boolean(message) && !dismissed;

  const restart = async () => {
    setRestarting(true);
    try {
      await window.ira?.agent.restart();
    } finally {
      setRestarting(false);
      setDismissed(true);
    }
  };

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          role="status"
          className="panel no-drag mx-5 mb-2 flex items-center gap-3 px-4 py-2.5"
          style={{
            borderColor: "rgb(245 158 11 / 0.35)",
            background: "rgb(66 46 12 / 0.45)",
          }}
        >
          <AlertTriangle size={15} className="shrink-0 text-warn" />
          <p className="flex-1 text-[12px] text-text/90" data-selectable>
            {message}
          </p>

          {window.ira && (
            <button
              type="button"
              onClick={() => void restart()}
              disabled={restarting}
              className="flex items-center gap-1.5 rounded-full border border-white/15 px-2.5 py-1 text-[11px] text-text transition-colors hover:bg-white/10 disabled:opacity-50"
            >
              <RotateCw size={11} className={restarting ? "animate-spin" : ""} />
              {restarting ? "Restarting" : "Restart IRA"}
            </button>
          )}

          <button
            type="button"
            onClick={() => setDismissed(true)}
            aria-label="Dismiss"
            className="grid size-6 place-items-center rounded-full text-mute transition-colors hover:bg-white/10 hover:text-text"
          >
            <X size={12} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
