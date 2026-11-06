import {
  Activity,
  Archive,
  FileText,
  Pin,
  Play,
  RefreshCw,
  Square,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type {
  IraEvent,
  SkillDetail,
  SkillRun,
  SkillsApi,
  SkillSummary,
  SkillSuggestion,
} from "@shared/types";
import type { ExecutionActivity } from "@/lib/execution";

type Props = {
  api?: SkillsApi;
  connected: boolean;
  activities: ExecutionActivity[];
  onSend: (text: string) => void;
  subscribeEvents?: (listener: (event: IraEvent) => void) => () => void;
};
const active = (item: ExecutionActivity) =>
  item.phase === "running" || item.phase === "verifying";
const titleCase = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1);
const runLabel = (run: SkillRun) =>
  run.status === "waiting"
    ? "Waiting for answer"
    : run.status === "done"
      ? run.verified
        ? "Verified"
        : "Unverified"
      : titleCase(run.status);

function WaitingQuestion({
  run,
  connected,
  send,
}: {
  run: SkillRun;
  connected: boolean;
  send: (text: string, message: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const sending = useRef(false);
  const canRespond =
    connected &&
    run.status === "waiting" &&
    Boolean(run.run_id.trim() && run.question_id?.trim() && run.question?.trim());
  const canAnswer = canRespond && !submitted && Boolean(answer.trim());

  return (
    <form
      className="skill-question"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canAnswer || sending.current) return;
        sending.current = true;
        setSubmitted(true);
        send(
          `Answer skill ${JSON.stringify(run.run_id)} question ${JSON.stringify(run.question_id)} with ${JSON.stringify(answer)}.`,
          "Answer sent. Waiting for IRA to continue.",
        );
      }}
    >
      <label>
        {run.question || "Your answer"}
        <textarea
          rows={2}
          value={answer}
          disabled={submitted}
          onChange={(event) => setAnswer(event.target.value)}
        />
      </label>
      <div className="skill-actions">
        <button
          type="submit"
          className="skill-button skill-primary"
          disabled={!canAnswer}
        >
          Answer
        </button>
        {submitted && (
          <button
            type="button"
            className="skill-button"
            disabled={!canRespond}
            onClick={() => {
              if (!canRespond) return;
              sending.current = false;
              setSubmitted(false);
            }}
          >
            Edit answer
          </button>
        )}
        <button
          type="button"
          className="skill-button"
          disabled={!connected || !run.run_id.trim()}
          aria-label={`Stop ${run.skill_name}`}
          onClick={() =>
            send(
              `Stop the skill run ${JSON.stringify(run.run_id)}.`,
              "Stop requested. Waiting for IRA to confirm.",
            )
          }
        >
          <Square size={13} /> Stop
        </button>
      </div>
      {submitted && (
        <p className="skill-hint">
          Answer sent. Waiting for IRA to continue. If IRA asks for a correction,
          edit your answer.
        </p>
      )}
    </form>
  );
}

export function Skills({
  api = window.ira?.skills,
  connected,
  activities,
  onSend,
  subscribeEvents,
}: Props) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [runs, setRuns] = useState<SkillRun[]>([]);
  const [suggestions, setSuggestions] = useState<SkillSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const alive = useRef(false);
  const request = useRef(0);
  const detailRequest = useRef(0);
  const mutation = useRef(false);

  const refresh = useCallback(async () => {
    const id = ++request.current;
    if (!api) {
      setError("Open the desktop app to load your skills.");
      setLoading(false);
      return;
    }
    try {
      const [catalog, history, review] = await Promise.all([
        api.list(true),
        api.history(),
        api.suggestions(),
      ]);
      if (!alive.current || id !== request.current) return;
      if ("error" in catalog) throw new Error(catalog.error);
      if ("error" in history) throw new Error(history.error);
      if ("error" in review) throw new Error(review.error);
      setSkills(catalog.skills);
      setRuns(history.runs);
      setSuggestions(review.suggestions);
      setError(null);
    } catch (cause) {
      if (alive.current && id === request.current)
        setError(
          cause instanceof Error
            ? cause.message
            : "Skills bridge unavailable. Try again.",
        );
    } finally {
      if (alive.current && id === request.current) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const unsubscribe = subscribeEvents?.((event) => {
      if (event.type === "skills.changed") void refresh();
    });
    const timer = setInterval(() => {
      void refresh();
    }, 5_000);
    return () => {
      alive.current = false;
      request.current++;
      detailRequest.current++;
      clearInterval(timer);
      unsubscribe?.();
    };
  }, [refresh, subscribeEvents]);

  const change = async (
    skill: SkillSummary,
    action: "pin" | "archive" | "restore",
  ) => {
    if (!api || mutation.current) return;
    mutation.current = true;
    setPending(skill.slug);
    setError(null);
    try {
      const result =
        action === "pin"
          ? await api.pin(skill.slug, !skill.pinned)
          : await api[action](skill.slug);
      if ("error" in result) throw new Error(result.error);
      if (!alive.current) return;
      setDetail(null);
      await refresh();
    } catch (cause) {
      if (alive.current)
        setError(
          cause instanceof Error ? cause.message : "Skill could not be saved.",
        );
    } finally {
      mutation.current = false;
      if (alive.current) setPending(null);
    }
  };

  const showDetail = async (slug: string, version?: number) => {
    if (!api) return;
    const id = ++detailRequest.current;
    setDetail(null);
    try {
      const result = await api.detail(slug, version);
      if (!alive.current || id !== detailRequest.current) return;
      if ("error" in result) throw new Error(result.error);
      setDetail(result.skill);
    } catch (cause) {
      if (alive.current && id === detailRequest.current)
        setError(
          cause instanceof Error ? cause.message : "Could not load this skill.",
        );
    }
  };

  const send = (text: string, message: string) => {
    onSend(text);
    setNotice(message);
  };
  return (
    <section className="skills-page panel" aria-labelledby="skills-title">
      <header className="skills-header">
        <div>
          <p className="label-caps">Automation</p>
          <h1 id="skills-title">Skills</h1>
          <p>Saved procedures, clear progress, and a record of every result.</p>
        </div>
        <button
          className="skill-button"
          onClick={() => void refresh()}
          aria-label="Refresh skills"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </header>
      <div role="status" aria-live="polite" className="skills-status">
        {loading
          ? "Loading your skills…"
          : (notice ??
            (!connected
              ? "Connect to IRA to run a skill."
              : `${skills.filter((skill) => !skill.archived).length} saved skills`))}
      </div>
      {error && (
        <p role="alert" className="skills-error">
          {error}
        </p>
      )}
      <div className="skills-columns">
        <div className="skills-catalog">
          {!loading && !skills.length && !error && (
            <p className="skills-empty">
              No skills saved yet. Ask IRA to save a repeatable task.
            </p>
          )}
          {skills.map((skill) => {
            const waiting = runs.some(
              (run) => run.skill_slug === skill.slug && run.status === "waiting",
            );
            const running = activities.find(
              (item) =>
                item.skillSlug === skill.slug &&
                active(item) &&
                !runs.some(
                  (run) =>
                    run.run_id === item.skillRunId && run.status === "waiting",
                ),
            );
            const runnable =
              connected &&
              !skill.archived &&
              skill.state === "enabled" &&
              skill.executable !== false &&
              !skill.issues?.length &&
              !waiting &&
              !running;
            return (
              <article
                className={`skill-card${skill.archived ? " is-archived" : ""}`}
                key={skill.slug}
              >
                <div className="skill-heading">
                  <Activity size={18} />
                  <h2>{skill.name}</h2>
                  <span className="skill-state">
                    {skill.archived ? "Archived" : titleCase(skill.state)}
                  </span>
                </div>
                <p>{skill.description}</p>
                <div className="skill-meta">
                  <span>Version {skill.version}</span>
                  <span>{skill.use_count} runs</span>
                  <span>{skill.failure_count} failures</span>
                  {skill.pinned && <span>Pinned</span>}
                </div>
                {skill.state === "draft" && (
                  <p className="skill-hint">
                    Draft — review and compile with IRA before running.
                  </p>
                )}
                {skill.issues?.map((issue) => (
                  <p className="skills-error" key={issue}>
                    {issue}
                  </p>
                ))}
                {waiting && (
                  <p className="skill-progress">Waiting for answer</p>
                )}
                {running && (
                  <p className="skill-progress">
                    {titleCase(running.phase)}
                    {running.progress ? ` · ${running.progress}` : ""}
                  </p>
                )}
                <div className="skill-actions">
                  {running?.skillRunId ? (
                    <button
                      className="skill-button skill-primary"
                      disabled={!connected}
                      aria-label={`Stop ${skill.name}`}
                      onClick={() =>
                        send(
                          `Stop the skill run ${JSON.stringify(running.skillRunId)}.`,
                          "Stop requested. Waiting for IRA to confirm.",
                        )
                      }
                    >
                      <Square size={13} /> Stop
                    </button>
                  ) : (
                    <button
                      className="skill-button skill-primary"
                      disabled={!runnable}
                      aria-label={`Run ${skill.name}`}
                      onClick={() =>
                        send(
                          `Run the saved skill ${JSON.stringify(skill.slug)}.`,
                          "Sent to IRA. Waiting for the run to start.",
                        )
                      }
                    >
                      <Play size={13} /> Run
                    </button>
                  )}
                  <button
                    className="skill-button"
                    aria-label={`Details for ${skill.name}`}
                    onClick={() => void showDetail(skill.slug)}
                  >
                    <FileText size={13} /> Details
                  </button>
                  <button
                    className="skill-button"
                    disabled={pending !== null || skill.archived}
                    aria-label={`${skill.pinned ? "Unpin" : "Pin"} ${skill.name}`}
                    onClick={() => void change(skill, "pin")}
                  >
                    <Pin size={13} /> {skill.pinned ? "Unpin" : "Pin"}
                  </button>
                  <button
                    className="skill-button"
                    disabled={pending !== null || Boolean(running) || waiting}
                    aria-label={`${skill.archived ? "Restore" : "Archive"} ${skill.name}`}
                    onClick={() =>
                      void change(skill, skill.archived ? "restore" : "archive")
                    }
                  >
                    {skill.archived ? (
                      <Undo2 size={13} />
                    ) : (
                      <Archive size={13} />
                    )}
                    {skill.archived ? "Restore" : "Archive"}
                  </button>
                </div>
              </article>
            );
          })}
          {detail && (
            <section
              className="skill-detail"
              aria-label={`Skill details: ${detail.name}`}
            >
              <div className="skill-heading">
                <h2>{detail.name}</h2>
                <button
                  className="skill-button"
                  onClick={() => {
                    detailRequest.current++;
                    setDetail(null);
                  }}
                >
                  Close details
                </button>
              </div>
              <p>Versions: {detail.versions.join(", ")}</p>
              <div className="skill-actions">
                <label>
                  Inspect version{" "}
                  <select
                    aria-label="Skill version"
                    value={detail.selected_version ?? detail.version}
                    onChange={(event) =>
                      void showDetail(detail.slug, Number(event.target.value))
                    }
                  >
                    {detail.versions.map((version) => (
                      <option key={version} value={version}>
                        {version}
                      </option>
                    ))}
                  </select>
                </label>
                {!detail.archived && detail.state !== "enabled" && (
                  <button
                    className="skill-button skill-primary"
                    disabled={!connected || Boolean(detail.issues?.length)}
                    aria-label={`Enable ${detail.name}`}
                    onClick={() =>
                      send(
                        `Enable the saved skill ${JSON.stringify(detail.slug)}.`,
                        "Enable requested. IRA will validate this skill before enabling it.",
                      )
                    }
                  >
                    Enable skill
                  </button>
                )}
              </div>
              <pre>{detail.markdown}</pre>
              {detail.diff && (
                <>
                  <h3>Changes to current version</h3>
                  <pre>{detail.diff}</pre>
                </>
              )}
            </section>
          )}
        </div>
        <aside className="skill-history" aria-label="Run history">
          <h2>Run history</h2>
          <p className="skill-hint">Completion requires observed evidence.</p>
          {!runs.length && (
            <p className="skills-empty">Run results will appear here.</p>
          )}
          {runs.map((run) => (
            <details
              key={run.run_id}
              className="skill-run"
              open={run.status === "waiting" || undefined}
            >
              <summary>
                <span>{run.skill_name}</span>
                <span className={`skill-outcome outcome-${run.status}`}>
                  {runLabel(run)}
                </span>
              </summary>
              <div className="skill-meta">
                <span>Version {run.skill_version}</span>
                <span>
                  {run.completed_steps}/{run.total_steps} steps
                </span>
              </div>
              <p>{run.message}</p>
              {run.status === "waiting" && (
                <WaitingQuestion
                  key={JSON.stringify([run.run_id, run.question_id, run.question])}
                  run={run}
                  connected={connected}
                  send={send}
                />
              )}
              <ol>
                {run.steps.map((step) => (
                  <li key={step.step_index}>
                    <strong>
                      Step {step.step_index}: {step.outcome}
                    </strong>
                    <p>{step.message}</p>
                    {step.evidence && <pre>{step.evidence}</pre>}
                    {step.check_evidence && <pre>{step.check_evidence}</pre>}
                  </li>
                ))}
              </ol>
            </details>
          ))}
          {suggestions.length > 0 && (
            <section
              className="skill-suggestions"
              aria-label="Review suggestions"
            >
              <h2>Review suggestions</h2>
              {suggestions.map((suggestion) => (
                <article key={suggestion.slug}>
                  <h3>
                    {skills.find((skill) => skill.slug === suggestion.slug)
                      ?.name ?? suggestion.slug}
                  </h3>
                  <p>{suggestion.reason}</p>
                  <button
                    className="skill-button"
                    onClick={() => void showDetail(suggestion.slug)}
                  >
                    Review skill
                  </button>
                </article>
              ))}
            </section>
          )}
        </aside>
      </div>
    </section>
  );
}
