import { UserRound } from "lucide-react";
import { motion } from "motion/react";

import type { UserProfile } from "@shared/types";
import { profileIdentity } from "@/lib/profile";
import { NAV_ITEMS, type NavId } from "@/lib/nav";

/**
 * Left navigation.
 *
 * Home and Settings are built. Other destinations are marked not-ready and
 * route to a labelled empty state -- deliberately visible rather than hidden,
 * so the shape of the finished app is legible while being honest that the
 * pages don't exist yet.
 */
export function Sidebar({
  active,
  onSelect,
  profile,
}: {
  active: NavId;
  onSelect: (id: NavId) => void;
  profile?: UserProfile | null;
}) {
  return (
    <nav className="sidebar-glass panel flex w-[206px] shrink-0 flex-col py-3">
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
                className={`relative flex w-full items-center gap-3.5 rounded-xl px-3.5 py-3 text-left text-[14px] transition-colors ${
                  isActive ? "font-medium text-text" : "text-dim hover:bg-white/[0.04] hover:text-text"
                }`}
              >
                {isActive && (
                  <motion.span
                    // The glowing pill from the reference, as one shared
                    // element across items, so it slides between entries
                    // instead of popping.
                    layoutId="nav-active"
                    className="absolute inset-0 rounded-xl"
                    style={{
                      background:
                        "linear-gradient(95deg, rgb(var(--secondary-rgb) / 0.34) 0%, rgb(var(--accent-rgb) / 0.1) 100%)",
                      border: "1px solid rgb(var(--accent-rgb) / 0.32)",
                      boxShadow:
                        "0 0 22px rgb(var(--secondary-rgb) / 0.25), inset 0 1px 0 rgb(255 255 255 / 0.06)",
                    }}
                    transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  />
                )}
                <item.icon
                  size={18}
                  strokeWidth={1.75}
                  className={`relative ${isActive ? "text-white" : ""}`}
                />
                <span className="relative flex-1 truncate">{item.label}</span>
                {!item.ready && (
                  <span className="relative size-1 rounded-full bg-mute/50" aria-hidden />
                )}
              </motion.button>
            </li>
          );
        })}
      </ul>

      <UserCard profile={profile} />
    </nav>
  );
}

/** Sidebar footer uses only the canonical profile returned by the memory store. */
function UserCard({ profile }: { profile?: UserProfile | null }) {
  const identity = profileIdentity(profile);
  return (
    <div className="mx-2.5 mt-3 flex items-center gap-2.5 rounded-xl border border-[var(--panel-stroke)] bg-white/[0.03] px-2.5 py-2.5">
      <span
        className="grid size-8 shrink-0 place-items-center rounded-full text-[13px] font-semibold text-deep"
        style={{ background: "linear-gradient(140deg,var(--color-cyan),var(--color-blue))" }}
      >
        {identity.initials || <UserRound size={16} aria-hidden="true" />}
      </span>
      <div className="min-w-0 flex-1 leading-tight">
        <p className="flex items-center gap-1 text-[12px] font-medium text-text">
          <span className="truncate" title={identity.name}>{identity.name}</span>
        </p>
        <p className="text-[10px] text-mute" title="Your profile in Settings">{profile?.name.trim() ? "Saved on this device" : "Set up in Settings"}</p>
      </div>
    </div>
  );
}
