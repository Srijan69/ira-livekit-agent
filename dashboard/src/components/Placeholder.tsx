import { motion } from "motion/react";

import { NAV_ITEMS, type NavId } from "@/lib/nav";

/**
 * The eight pages that don't exist yet.
 *
 * Labelled rather than hidden, and explicit about it. A nav item that silently
 * does nothing when clicked is indistinguishable from a bug; one that says
 * "not built yet" is a roadmap.
 */
export function Placeholder({ id }: { id: NavId }) {
  const item = NAV_ITEMS.find((entry) => entry.id === id);
  if (!item) return null;

  return (
    <motion.div
      key={id}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4"
    >
      <span className="panel grid size-14 place-items-center rounded-2xl text-cyan/70">
        <item.icon size={24} strokeWidth={1.5} />
      </span>
      <div className="text-center">
        <h2 className="text-[17px] font-semibold text-text">{item.label}</h2>
        <p className="mt-1.5 max-w-[320px] text-[12.5px] leading-relaxed text-mute">
          Not built yet. Home is the only page in this build &mdash; ask IRA for
          anything on this page out loud in the meantime.
        </p>
      </div>
    </motion.div>
  );
}
