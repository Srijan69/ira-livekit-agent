import { motion } from "motion/react";

import { QUICK_ACTIONS } from "@/lib/nav";

/**
 * The seven action cards.
 *
 * Each one sends its configured prompt to IRA -- the same path a spoken
 * sentence takes, which is why clicking "Automation" gets you her voice
 * answering rather than a modal. Hover lifts and brightens on `transform` and
 * `opacity` only, so seven cards animating costs nothing.
 */
export function QuickActions({ onSend }: { onSend: (prompt: string) => void }) {
  return (
    <ul className="grid grid-cols-7 gap-2.5">
      {QUICK_ACTIONS.map((action, index) => (
        <li key={action.id}>
          <motion.button
            type="button"
            onClick={() => onSend(action.prompt)}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              duration: 0.4,
              delay: 0.3 + index * 0.045,
              ease: [0.22, 1, 0.36, 1],
            }}
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.985 }}
            className="group panel flex w-full flex-col items-start gap-2.5 px-3.5 py-3 text-left transition-shadow"
            style={{
              background: `linear-gradient(150deg, ${action.accent}1F 0%, rgb(13 25 45 / 0.55) 62%)`,
              borderColor: `${action.accent}33`,
            }}
          >
            <span
              className="grid size-8 place-items-center rounded-lg transition-transform group-hover:scale-105"
              style={{ background: `${action.accent}26`, color: action.accent }}
            >
              <action.icon size={16} strokeWidth={1.9} />
            </span>
            <span className="leading-tight">
              <span className="block text-[12.5px] font-semibold text-text">
                {action.label}
              </span>
              <span className="block text-[10px] text-mute">{action.sub}</span>
            </span>
          </motion.button>
        </li>
      ))}
    </ul>
  );
}
