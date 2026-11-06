import {
  Activity,
  Check,
  ChevronDown,
  Circle,
  CircleAlert,
  CircleSlash,
  LoaderCircle,
  Pause,
} from "lucide-react";
import { useId, useState } from "react";

import {
  isExecutionActive as isActive,
  safeToolName,
  type ExecutionActivity,
  type ExecutionStep,
} from "@/lib/execution";
import type { OrbState } from "@/lib/orbState";

// A bowed surface, shared by the glass clipping and its illuminated rim.
// Keep the contents as HTML so selection, scrolling and keyboard controls work.
const glassOutline = "M .055 .014 C .34 .069 .68 .084 .948 .071 Q .985 .069 .985 .106 L .985 .9 Q .985 .937 .948 .946 C .65 .986 .32 1 .055 .975 Q .014 .97 .014 .932 L .014 .05 Q .014 .007 .055 .014 Z";

function ExecutionGlass({ id }: { id: string }) {
  const clipId = `execution-clip-${id}`;
  const lightId = `execution-light-${id}`;
  return (
    <div className="execution-glass" aria-hidden="true">
      <div className="execution-glass-surface" style={{ clipPath: `url(#${clipId})` }} />
      <svg viewBox="0 0 1 1" preserveAspectRatio="none" focusable="false">
        <defs>
          <clipPath id={clipId} clipPathUnits="objectBoundingBox">
            <path d={glassOutline} />
          </clipPath>
          <linearGradient id={lightId} x1="0" y1="0" x2="1" y2="0.6" gradientUnits="userSpaceOnUse">
            <stop stopColor="var(--color-cyan)" stopOpacity=".95" />
            <stop offset=".5" stopColor="var(--color-cyan)" stopOpacity=".38" />
            <stop offset="1" stopColor="var(--color-cyan)" stopOpacity=".75" />
          </linearGradient>
        </defs>
        <path className="execution-glass-depth" d={glassOutline} />
        <path className="execution-glass-bloom" d={glassOutline} />
        <path className="execution-glass-rim" d={glassOutline} stroke={`url(#${lightId})`} />
      </svg>
    </div>
  );
}

const phaseLabels: Record<ExecutionActivity["phase"], string> = {
  running: "Running",
  verifying: "Verifying",
  waiting: "Waiting for answer",
  finished: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
  uncertain: "Uncertain",
  unavailable: "Unavailable",
};

const sessionLabels: Record<OrbState, string> = {
  offline: "Offline",
  connecting: "Connecting",
  listening: "Listening",
  recognizing: "Listening",
  generating: "Thinking",
  speaking: "Responding",
  executing: "Working",
  error: "Attention needed",
};

function PhaseIcon({ phase }: { phase: ExecutionActivity["phase"] }) {
  const Icon =
    phase === "finished"
      ? Check
      : phase === "failed" || phase === "unavailable" || phase === "uncertain"
        ? CircleAlert
        : phase === "cancelled"
          ? CircleSlash
          : phase === "waiting"
            ? Pause
            : phase === "verifying"
              ? Circle
              : LoaderCircle;
  return <Icon size={12} strokeWidth={1.8} aria-hidden="true" />;
}

function Duration({
  entry,
  now,
}: {
  entry: Pick<ExecutionActivity, "phase" | "startedAt" | "endedAt">;
  now: number;
}) {
  const elapsed = Math.max(
    0,
    Math.floor(((entry.endedAt ?? now) - entry.startedAt) / 1_000),
  );
  return (
    <span
      className="execution-duration"
      aria-label={`${elapsed} seconds${isActive(entry) ? " elapsed" : " total"}`}
    >
      {elapsed}s
    </span>
  );
}

function SkillSteps({ steps, now }: { steps: ExecutionStep[]; now: number }) {
  return (
    <>
      {steps[0].index > 1 && (
        <p className="execution-progress">Earlier steps omitted</p>
      )}
      <ol className="execution-steps" aria-label="Skill steps">
        {steps.map((step) => (
          <li
            key={step.index}
            className={`execution-step execution-phase-${step.phase}`}
          >
            <span
              className="execution-step-number"
              aria-label={`Step ${step.index}`}
            >
              {step.index}
            </span>
            <div className="execution-row-copy">
              <div className="execution-step-heading">
                <p className="execution-step-title">{step.title}</p>
                <Duration entry={step} now={now} />
              </div>
              <div className="execution-row-meta">
                {safeToolName(step.toolName) && <code>{step.toolName}</code>}
                <span className="execution-phase">
                  <PhaseIcon phase={step.phase} />
                  {phaseLabels[step.phase]}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

export function ExecutionStatus({
  activities,
  state,
  now,
  onClear,
  animate,
}: {
  activities: ExecutionActivity[];
  state: OrbState;
  now: number;
  onClear: () => void;
  animate: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const id = useId();
  const headingId = `execution-heading-${id}`;
  const historyId = `execution-history-${id}`;
  const connected = state !== "offline" && state !== "connecting";
  const sorted = [...activities].sort(
    (a, b) =>
      Number(isActive(b)) - Number(isActive(a)) || b.updatedAt - a.updatedAt,
  );
  const current = connected ? sorted.find(isActive) : undefined;
  const currentStep = current?.steps?.slice().reverse().find(isActive);
  const canClear = activities.some((entry) => !isActive(entry));
  const description =
    state === "offline"
      ? "Connect to see IRA's actions"
      : state === "connecting"
        ? "Waiting for IRA to join"
        : current
          ? current.phase === "waiting"
            ? "Waiting for your answer to continue"
            : current.phase === "verifying"
              ? "Checking the result"
              : "Action in progress"
          : activities.length === 0
            ? "Actions appear here as IRA works"
            : "Ready for your next request";

  return (
    <section
      className="execution-panel"
      data-animate={animate}
      data-expanded={expanded}
      data-has-steps={!!current?.steps?.length}
      aria-labelledby={headingId}
    >
      <ExecutionGlass id={id} />
      <header className="execution-header">
        <button
          type="button"
          className="execution-toggle"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={historyId}
          aria-label={`${expanded ? "Collapse" : "Expand"} execution status`}
        >
          <Activity size={14} strokeWidth={1.6} aria-hidden="true" />
          <h2 id={headingId}>Execution status</h2>
          <ChevronDown
            className="execution-chevron"
            size={13}
            aria-hidden="true"
          />
        </button>
        {connected && (
          <span className="execution-live">
            <i aria-hidden="true" />
            LIVE
          </span>
        )}
      </header>

      <div
        className="execution-current"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        <span
          className={`execution-current-dot${current && current.phase !== "waiting" ? " is-active" : ""}${!connected ? " is-offline" : ""}`}
          aria-hidden="true"
        />
        <div className="execution-current-copy">
          <span className="execution-eyebrow">
            {current ? "Current action" : "IRA status"}
          </span>
          <p className="execution-current-title">
            {current?.title ?? sessionLabels[state]}
          </p>
          {currentStep && (
            <p className="execution-tool">
              {safeToolName(currentStep.toolName) ? <code>{currentStep.toolName}</code> : currentStep.title}
            </p>
          )}
          <p className="execution-current-detail">{description}</p>
        </div>
      </div>

      <div id={historyId} className="execution-history" hidden={!expanded}>
        {sorted.length > 0 && (
          <>
            <div className="execution-history-heading">
              <span>Recent activity</span>
              <span className="execution-count">{sorted.length}</span>
            </div>
            <ul
              className="execution-list"
              aria-label="Recent actions"
              tabIndex={0}
            >
              {sorted.map((entry) => (
                <li
                  key={entry.id}
                  className={`execution-row execution-phase-${entry.phase}`}
                  data-current={entry.id === current?.id && !!entry.steps?.length}
                >
                  <span className="execution-row-icon">
                    <PhaseIcon phase={entry.phase} />
                  </span>
                  <div className="execution-row-copy" data-selectable>
                    <h3>{entry.title}</h3>
                    {safeToolName(entry.toolName) && (
                      <code className="execution-tool">{entry.toolName}</code>
                    )}
                    {entry.progress && (
                      <p className="execution-progress">{entry.progress}</p>
                    )}
                    <div className="execution-row-meta">
                      <span className="execution-phase">
                        {phaseLabels[entry.phase]}
                      </span>
                      <Duration entry={entry} now={now} />
                    </div>
                    {!!entry.steps?.length && (
                      <SkillSteps steps={entry.steps} now={now} />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
        <footer className="execution-footer">
          <span>{sorted.length ? "This session" : "No actions yet"}</span>
          <button type="button" onClick={onClear} disabled={!canClear}>
            Clear finished
          </button>
        </footer>
      </div>
    </section>
  );
}
