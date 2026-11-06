import { useState } from "react";

import { Backdrop } from "./components/Backdrop";
import { CommandBar } from "./components/CommandBar";
import { Placeholder } from "./components/Placeholder";
import { QuickActions } from "./components/QuickActions";
import { Sidebar } from "./components/Sidebar";
import { Settings } from "./components/Settings";
import { Skills } from "./components/Skills";
import { Stage } from "./components/Stage";
import { SuggestionChips } from "./components/SuggestionChips";
import { StatusBanner } from "./components/StatusBanner";
import { SystemStatus } from "./components/SystemStatus";
import { TopBar } from "./components/TopBar";
import { ThemeRestart } from "./components/ThemeRestart";
import { UpcomingTasks } from "./components/UpcomingTasks";
import { WeatherCard } from "./components/WeatherCard";
import { useAnimationEnabled } from "./hooks/useAnimationEnabled";
import { useClock } from "./hooks/useClock";
import { useSession } from "./hooks/useSession";
import { useSystemStats } from "./hooks/useSystemStats";
import { useTasks } from "./hooks/useTasks";
import { useTheme } from "./hooks/useTheme";
import { useWeather } from "./hooks/useWeather";
import { usePersonalMemory } from "./hooks/usePersonalMemory";
import type { NavId } from "./lib/nav";

export function App() {
  const [page, setPage] = useState<NavId>("home");
  const theme = useTheme();

  // Two clocks at different rates. The header needs every second; due labels
  // only change on the minute, and re-rendering the task list 60 times a
  // minute to recompute identical strings is the kind of waste that adds up
  // to a warm laptop.
  const now = useClock(1_000);
  const coarseNow = useClock(30_000);

  const stats = useSystemStats();
  const animate = useAnimationEnabled();
  const session = useSession();

  const tasks = useTasks({
    subscribeEvents: session.subscribeEvents,
    connection: session.connection,
  });
  const weather = useWeather();
  const personalMemory = usePersonalMemory();

  return (
    <div
      className={`ira-dashboard relative flex h-full flex-col overflow-hidden ${page !== "home" ? "interior-page" : ""}`}
    >
      <Backdrop theme={theme.activeTheme} />

      <div
        className="relative z-10 flex min-h-0 flex-1 flex-col"
        inert={theme.pendingTheme !== null}
      >
        <TopBar
          now={now}
          connection={session.connection}
          micEnabled={session.micEnabled}
          onToggleMic={session.toggleMic}
        />

        <StatusBanner error={session.error} />

        <div className="dashboard-layout flex min-h-0 flex-1 gap-3.5 px-5 pb-4">
          <Sidebar active={page} onSelect={setPage} profile={personalMemory.profile} />

          {page === "home" ? (
            <main className="flex min-h-0 flex-1 flex-col gap-3.5">
              <div className="dashboard-home-top flex min-h-0 flex-1 gap-3.5">
                <Stage
                  state={session.orbState}
                  cues={session.cues}
                  animate={animate}
                  getLevel={session.getLevel}
                  activities={session.activities}
                  now={now.getTime()}
                  onClearExecution={session.clearExecutionHistory}
                />

                <aside className="dashboard-rail flex w-[352px] shrink-0 flex-col gap-3.5">
                  <SystemStatus stats={stats} delay={0.08} />
                  <WeatherCard
                    {...weather}
                    onLocationChange={weather.setLocation}
                    delay={0.16}
                  />
                  <UpcomingTasks
                    tasks={tasks.tasks}
                    now={coarseNow}
                    onToggle={tasks.toggle}
                    loading={tasks.loading}
                    error={tasks.error}
                    pendingIds={tasks.pendingIds}
                    expanded={tasks.expanded}
                    onExpand={() => tasks.setExpanded(!tasks.expanded)}
                    onRetry={tasks.refresh}
                    delay={0.24}
                  />
                </aside>
              </div>

              {/* Centred under both the stage and the rail, as in the
                  reference, rather than confined to the stage column. */}
              <div className="dashboard-actions mx-auto w-full max-w-[1130px] space-y-3">
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
          ) : page === "automation" ? (
            <main className="flex min-h-0 min-w-0 flex-1">
              <Skills connected={session.connection === "connected"} onSend={session.send} activities={session.activities} subscribeEvents={session.subscribeEvents} />
            </main>
          ) : page === "settings" ? (
            <main className="flex min-h-0 min-w-0 flex-1">
              <Settings
                activeTheme={theme.activeTheme}
                onSelect={theme.selectTheme}
                error={theme.error}
                personalMemory={personalMemory}
              />
            </main>
          ) : (
            <main className="flex min-h-0 flex-1">
              <Placeholder id={page} />
            </main>
          )}
        </div>
      </div>
      {theme.pendingTheme && <ThemeRestart theme={theme.pendingTheme} />}
    </div>
  );
}
