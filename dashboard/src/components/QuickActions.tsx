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
    <ul className="quick-actions grid grid-cols-7 gap-2.5">
      {QUICK_ACTIONS.map((action, index) => {
        const accent = index < 4 ? "var(--color-cyan)" : action.accent;
        return (
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
              className="group panel flex w-full flex-col items-center gap-2.5 px-3 pt-4 pb-3.5 text-center transition-shadow"
              style={{
                background: `linear-gradient(150deg, color-mix(in srgb, ${accent} 15%, transparent) 0%, color-mix(in srgb, var(--color-deep) 92%, transparent) 75%)`,
                borderColor: `color-mix(in srgb, ${accent} 30%, transparent)`,
                boxShadow: `inset 0 1px 0 0 color-mix(in srgb, ${accent} 20%, transparent), 0 12px 30px rgb(0 0 0 / 0.45)`,
              }}
            >
              <span
                className="grid size-10 place-items-center rounded-xl transition-transform group-hover:scale-105"
                style={{
                  background: `color-mix(in srgb, ${accent} 15%, transparent)`,
                  color: accent,
                  boxShadow: `0 0 18px color-mix(in srgb, ${accent} 20%, transparent)`,
                }}
              >
                <action.icon size={20} strokeWidth={1.8} />
              </span>
              <span className="leading-tight">
                <span className="block text-[13px] font-semibold text-text">
                  {action.label}
                </span>
                <span className="mt-0.5 block text-[10.5px] text-dim">{action.sub}</span>
              </span>
            </motion.button>
          </li>
        );
      })}
    </ul>
  );
}
