import { motion } from "motion/react";

import { SUGGESTIONS } from "@/lib/nav";

/** Pre-filled prompts. A chip is just a canned sentence down the same path. */
export function SuggestionChips({ onSend }: { onSend: (prompt: string) => void }) {
  return (
    <ul className="flex flex-wrap items-center justify-center gap-2">
      {SUGGESTIONS.map((suggestion, index) => (
        <li key={suggestion.id}>
          <motion.button
            type="button"
            onClick={() => onSend(suggestion.prompt)}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              duration: 0.35,
              delay: 0.6 + index * 0.04,
              ease: [0.22, 1, 0.36, 1],
            }}
            whileHover={{ y: -1 }}
            whileTap={{ scale: 0.97 }}
            className="panel rounded-full px-3 py-1.5 text-[11px] text-dim transition-colors hover:border-cyan/30 hover:text-text"
          >
            {suggestion.label}
          </motion.button>
        </li>
      ))}
    </ul>
  );
}
