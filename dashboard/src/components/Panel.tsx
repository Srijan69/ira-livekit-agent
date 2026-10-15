import { motion } from "motion/react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * The glass card every panel is built from.
 *
 * Entrance animates `opacity` and `y` only -- both compositor properties, so
 * a dashboard with eight panels appearing at once doesn't drop frames on the
 * first paint. `delay` is what staggers them.
 */
export function Panel({
  children,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: [0.22, 1, 0.36, 1] }}
      className={`panel ${className}`}
    >
      {children}
    </motion.section>
  );
}

/** Icon, title, and an optional control on the right -- as every card in the
 *  reference image has. */
export function PanelHeader({
  icon: Icon,
  title,
  action,
}: {
  icon: LucideIcon;
  title: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex items-center gap-2.5 px-4 pt-3.5 pb-2">
      <span className="grid size-7 place-items-center rounded-lg bg-white/5 text-cyan">
        <Icon size={15} strokeWidth={1.75} />
      </span>
      <h2 className="flex-1 text-[13px] font-semibold tracking-tight text-text">
        {title}
      </h2>
      {action}
    </header>
  );
}
