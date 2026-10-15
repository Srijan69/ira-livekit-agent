import { useCallback, useState } from "react";

import { Backdrop } from "./components/Backdrop";
import { CommandBar } from "./components/CommandBar";
import { Placeholder } from "./components/Placeholder";
import { QuickActions } from "./components/QuickActions";
import { Sidebar } from "./components/Sidebar";
import { Stage } from "./components/Stage";
import { SuggestionChips } from "./components/SuggestionChips";
import { StatusBanner } from "./components/StatusBanner";
import { SystemStatus } from "./components/SystemStatus";
import { TopBar } from "./components/TopBar";
import { UpcomingTasks } from "./components/UpcomingTasks";
import { WeatherCard } from "./components/WeatherCard";
import { useAnimationEnabled } from "./hooks/useAnimationEnabled";
import { useClock } from "./hooks/useClock";
import { useSession } from "./hooks/useSession";
import { useSystemStats } from "./hooks/useSystemStats";
import { MOCK_TASKS, MOCK_WEATHER, type Task } from "./lib/mock";
import type { NavId } from "./lib/nav";

export function App() {
  const [page, setPage] = useState<NavId>("home");

  // Two clocks at different rates. The header needs every second; due labels
  // only change on the minute, and re-rendering the task list 60 times a
  // minute to recompute identical strings is the kind of waste that adds up
  // to a warm laptop.
  const now = useClock(1_000);
  const coarseNow = useClock(30_000);

  const stats = useSystemStats();
  const animate = useAnimationEnabled();
  const session = useSession();

  // Mock until the bridge exists in sub-project C. The handler is already the
  // real one, so only the data source changes.
  const [tasks, setTasks] = useState<Task[]>(MOCK_TASKS);
  const toggleTask = useCallback((id: string) => {
    setTasks((current) =>
      current.map((task) => (task.id === id ? { ...task, done: !task.done } : task)),
    );
  }, []);

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <Backdrop />

      <div className="relative z-10 flex min-h-0 flex-1 flex-col">
        <TopBar
          now={now}
          connection={session.connection}
          micEnabled={session.micEnabled}
          onToggleMic={session.toggleMic}
        />

        <StatusBanner error={session.error} />

        <div className="flex min-h-0 flex-1 gap-3.5 px-5 pb-4">
          <Sidebar active={page} onSelect={setPage} />

          {page === "home" ? (
            <main className="flex min-h-0 flex-1 flex-col gap-3.5">
              <div className="flex min-h-0 flex-1 gap-3.5">
                <Stage
                  state={session.orbState}
                  cues={session.cues}
                  animate={animate}
                  getLevel={session.getLevel}
                />

                <aside className="flex w-[352px] shrink-0 flex-col gap-3.5">
                  <SystemStatus stats={stats} delay={0.08} />
                  <WeatherCard weather={MOCK_WEATHER} delay={0.16} />
                  <UpcomingTasks
                    tasks={tasks}
                    now={coarseNow}
                    onToggle={toggleTask}
                    delay={0.24}
                  />
                </aside>
              </div>

              {/* Centred under both the stage and the rail, as in the
                  reference, rather than confined to the stage column. */}
              <div className="mx-auto w-full max-w-[1130px] space-y-3">
                <QuickActions onSend={session.send} />
                <CommandBar
                  onSend={session.send}
                  disabled={session.connection === "disconnected"}
                  micEnabled={session.micEnabled}
                  onToggleMic={session.toggleMic}
                />
                <SuggestionChips onSend={session.send} />
              </div>
            </main>
          ) : (
            <main className="flex min-h-0 flex-1">
              <Placeholder id={page} />
            </main>
          )}
        </div>
      </div>
    </div>
  );
}
