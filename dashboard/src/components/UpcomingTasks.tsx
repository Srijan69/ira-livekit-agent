import { Check, ClipboardList } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { formatDue } from "@/lib/format";
import type { Task } from "@shared/types";

import { Panel, PanelHeader } from "./Panel";

/**
 * The task list.
 *
 * `now` is a prop, fed by a deliberately coarse clock: due labels only change
 * on the minute, so re-rendering this list every second to recompute identical
 * strings is pure waste. It also means the labels re-derive on a tick rather
 * than at mount -- which is what stops a dashboard left open overnight from
 * still describing this morning as "Tomorrow".
 */
export function UpcomingTasks({
  tasks,
  now,
  onToggle,
  onExpand,
  onRetry,
  expanded = false,
  loading = false,
  error = null,
  pendingIds,
  delay = 0,
}: {
  tasks: Task[];
  now: Date;
  onToggle: (id: string) => void;
  onExpand?: () => void;
  onRetry?: () => void;
  expanded?: boolean;
  loading?: boolean;
  error?: string | null;
  pendingIds?: ReadonlySet<string>;
  delay?: number;
}) {
  const visible = tasks.slice(0, expanded ? 100 : 8);

  return (
    <Panel delay={delay} className="flex min-h-0 flex-col">
      <PanelHeader
        icon={ClipboardList}
        title="Upcoming Tasks"
        action={
          <button
            type="button"
            onClick={onExpand}
            aria-expanded={expanded}
            className="text-[11px] font-medium text-cyan/90 transition-colors hover:text-cyan"
          >
            {expanded ? "Show Less" : "View All"}
          </button>
        }
      />

      {error && (
        <div role="status" className="px-4 pb-2 text-[11px] text-warn">
          {error}{" "}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="text-cyan underline"
            >
              Retry
            </button>
          )}
        </div>
      )}

      <ul className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3">
        <AnimatePresence initial={false}>
          {visible.map((task) => {
            const due = formatDue(
              task.due_at ? new Date(task.due_at) : null,
              now,
            );
            return (
              <motion.li
                key={task.id}
                layout
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 10 }}
                transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                className="flex items-center gap-2.5 rounded-lg px-1.5 py-2 transition-colors hover:bg-white/[0.04]"
              >
                <button
                  type="button"
                  onClick={() => onToggle(task.id)}
                  disabled={pendingIds?.has(task.id)}
                  role="checkbox"
                  aria-checked={task.done}
                  aria-label={
                    task.done
                      ? `Reopen "${task.title}"`
                      : `Mark "${task.title}" done`
                  }
                  className={`grid size-[17px] shrink-0 place-items-center rounded-[5px] border border-white/20 transition-colors hover:border-cyan disabled:opacity-50 ${task.done ? "text-cyan" : "text-transparent hover:text-cyan/70"}`}
                >
                  <Check size={11} strokeWidth={3} />
                </button>

                <span className="min-w-0 flex-1 truncate text-[12px] text-text/90">
                  {task.title}
                </span>

                <span
                  className={`tnum shrink-0 text-[10.5px] ${
                    due.overdue ? "font-medium text-warn" : "text-mute"
                  }`}
                >
                  {due.label}
                </span>
              </motion.li>
            );
          })}
        </AnimatePresence>

        {visible.length === 0 && !error && (
          <li className="flex h-[88px] items-center justify-center">
            <p className="label-caps">
              {loading ? "Loading tasks…" : "Nothing due, boss"}
            </p>
          </li>
        )}
      </ul>
    </Panel>
  );
}
