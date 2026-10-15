import { motion } from "motion/react";

import { greetingFor } from "@/lib/format";

/**
 * "Good Evening, Boss."
 *
 * Boss, not Sir. IRA's persona addresses the user as "boss" and a test in the
 * Python suite enforces it -- a header saying "Sir" while she says "boss"
 * would be the UI contradicting the agent.
 */
export function Greeting({ now }: { now: Date }) {
  const greeting = greetingFor(now);

  return (
    <div className="text-center">
      <motion.h1
        // Keyed on the greeting so it cross-fades when the hour rolls over
        // rather than snapping from Evening to Night mid-session.
        key={greeting}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="text-[25px] font-semibold tracking-tight text-text"
      >
        {greeting}, Boss
      </motion.h1>
      <p className="mt-0.5 text-[13px] text-dim">How can I help you today?</p>
    </div>
  );
}
