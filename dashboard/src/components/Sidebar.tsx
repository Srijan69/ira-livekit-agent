import { Crown } from "lucide-react";
import { motion } from "motion/react";

import { MOCK_USER } from "@/lib/mock";
import { NAV_ITEMS, type NavId } from "@/lib/nav";

/**
 * Left navigation.
 *
 * Only Home is built. The other eight are rendered but marked not-ready and
 * route to a labelled empty state -- deliberately visible rather than hidden,
 * so the shape of the finished app is legible while being honest that the
 * pages don't exist yet.
 */
export function Sidebar({
  active,
  onSelect,
}: {
  active: NavId;
  onSelect: (id: NavId) => void;
}) {
  return (
    <nav className="panel flex w-[198px] shrink-0 flex-col py-3">
      <ul className="flex-1 space-y-0.5 px-2.5">
        {NAV_ITEMS.map((item, index) => {
          const isActive = item.id === active;
          return (
            <li key={item.id}>
              <motion.button
                type="button"
                onClick={() => onSelect(item.id)}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{
                  duration: 0.35,
                  delay: 0.1 + index * 0.035,
                  ease: [0.22, 1, 0.36, 1],
                }}
                aria-current={isActive ? "page" : undefined}
                className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] transition-colors ${
                  isActive
                    ? "bg-white/[0.07] font-medium text-text"
                    : "text-dim hover:bg-white/[0.04] hover:text-text"
                }`}
              >
                {isActive && (
                  <motion.span
                    // One shared element across list items, so the accent
                    // slides between entries instead of popping.
                    layoutId="nav-accent"
                    className="absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-cyan"
                    style={{ boxShadow: "var(--glow-cyan-soft)" }}
                    transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  />
                )}
                <item.icon
                  size={16}
                  strokeWidth={1.75}
                  className={isActive ? "text-cyan" : ""}
                />
                <span className="flex-1 truncate">{item.label}</span>
                {!item.ready && (
                  <span className="size-1 rounded-full bg-mute/50" aria-hidden />
                )}
              </motion.button>
            </li>
          );
        })}
      </ul>

      <UserCard />
    </nav>
  );
}

/** Sidebar footer. Name comes from mock data until Settings exists to own it. */
function UserCard() {
  return (
    <div className="mx-2.5 mt-3 flex items-center gap-2.5 rounded-xl border border-[var(--panel-stroke)] bg-white/[0.03] px-2.5 py-2.5">
      <span
        className="grid size-8 shrink-0 place-items-center rounded-full text-[13px] font-semibold text-deep"
        style={{ background: "linear-gradient(140deg,#67E8F9,#2E7BFF)" }}
      >
        {MOCK_USER.name.charAt(0)}
      </span>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="flex items-center gap-1 text-[12px] font-medium text-text">
          <span className="truncate">{MOCK_USER.name}</span>
          <Crown size={11} className="shrink-0 text-warn" />
        </p>
        <p className="text-[10px] text-mute">{MOCK_USER.tier}</p>
      </div>
    </div>
  );
}
