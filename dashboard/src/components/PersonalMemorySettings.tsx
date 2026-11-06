import {
  Check,
  Database,
  LoaderCircle,
  Search,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import type { MemorySettings, UserProfile } from "@shared/types";
import type { PersonalMemoryState } from "@/hooks/usePersonalMemory";

type ProfileDraft = {
  name: string;
  age: string;
  occupation: string;
  preferences: string;
};
const blankProfile: ProfileDraft = {
  name: "",
  age: "",
  occupation: "",
  preferences: "",
};
function profileDraft(profile: UserProfile): ProfileDraft {
  return {
    name: profile.name,
    age: profile.age === null ? "" : String(profile.age),
    occupation: profile.occupation,
    preferences: profile.preferences,
  };
}

function Feedback({
  error,
  success,
}: {
  error: string | null;
  success: string | null;
}) {
  return (
    <>
      {error && (
        <p role="alert" className="connection-feedback is-error">
          {error}
        </p>
      )}
      {success && (
        <p role="status" className="connection-feedback is-success">
          <Check size={15} />
          {success}
        </p>
      )}
    </>
  );
}

function Field({
  id,
  label,
  help,
  error,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="personal-memory-field">
      <label htmlFor={id}>{label}</label>
      {children}
      {help && (
        <p id={`${id}-help`} className="connection-field-help">
          {help}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="connection-field-error">
          {error}
        </p>
      )}
    </div>
  );
}

export function ProfileSettings({ memory }: { memory: PersonalMemoryState }) {
  const [draft, setDraft] = useState<ProfileDraft>(blankProfile);
  const [revision, setRevision] = useState<number | undefined>();
  const [errors, setErrors] = useState<
    Partial<Record<keyof ProfileDraft, string>>
  >({});
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const dirty = useRef(false);
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (memory.profile && !dirty.current) {
      setDraft(profileDraft(memory.profile));
      setRevision(memory.profile.revision);
    }
  }, [memory.profile]);

  function change(field: keyof ProfileDraft, value: string) {
    dirty.current = true;
    setDraft((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
    setError(null);
    setSuccess(null);
  }

  function acceptProfile(profile: UserProfile) {
    dirty.current = false;
    setDraft(profileDraft(profile));
    setRevision(profile.revision);
    setErrors({});
    setError(null);
    setConflict(false);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !memory.available ||
      memory.profileLoading ||
      memory.savingProfile ||
      !memory.profile
    )
      return;
    const nextErrors: Partial<Record<keyof ProfileDraft, string>> = {};
    const age = draft.age.trim() ? Number(draft.age) : null;
    if (age !== null && (!Number.isInteger(age) || age < 0 || age > 130))
      nextErrors.age =
        "Enter a whole number from 0 to 130, or leave age blank.";
    if (draft.name.length > 120)
      nextErrors.name = "Keep your name within 120 characters.";
    if (draft.occupation.length > 200)
      nextErrors.occupation = "Keep your occupation within 200 characters.";
    if (draft.preferences.length > 4000)
      nextErrors.preferences = "Keep preferences within 4,000 characters.";
    setErrors(nextErrors);
    setSuccess(null);
    const first = Object.keys(nextErrors)[0] as keyof ProfileDraft | undefined;
    if (first) {
      setError(nextErrors[first]!);
      const element = form.current?.elements.namedItem(first);
      if (element instanceof HTMLElement) element.focus();
      return;
    }
    setError(null);
    const result = await memory.saveProfile({ ...draft, age }, revision);
    if ("error" in result) {
      setConflict(Boolean(result.conflict));
      setError(
        result.conflict
          ? `${result.error} Your edits are kept. Reload the saved profile to review the latest changes.`
          : result.error,
      );
    } else {
      acceptProfile(result);
      setConfirmReset(false);
      setSuccess(
        "Profile saved. IRA will use your changes at the next quiet moment.",
      );
    }
  }

  async function reset() {
    setError(null);
    setSuccess(null);
    const result = await memory.resetProfile();
    if ("error" in result) setError(result.error);
    else {
      acceptProfile(result);
      setConfirmReset(false);
      setSuccess("Profile reset. Your profile fields are now empty.");
    }
  }

  const disabled =
    !memory.available ||
    memory.profileLoading ||
    memory.savingProfile ||
    !memory.profile ||
    revision === undefined;
  const input = (
    field: keyof ProfileDraft,
    label: string,
    help?: string,
    maxLength?: number,
  ) => {
    const id = `profile-${field}`;
    const properties = {
      id,
      name: field,
      value: draft[field],
      onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        change(field, event.target.value),
      "aria-invalid": Boolean(errors[field]),
      "aria-describedby":
        [help && `${id}-help`, errors[field] && `${id}-error`]
          .filter(Boolean)
          .join(" ") || undefined,
      maxLength,
    };
    return (
      <Field id={id} label={label} help={help} error={errors[field]}>
        {field === "preferences" ? (
          <textarea
            {...properties}
            rows={5}
            placeholder="How you like IRA to help, interests, or things to remember"
          />
        ) : (
          <input
            {...properties}
            type={field === "age" ? "number" : "text"}
            min={field === "age" ? 0 : undefined}
            max={field === "age" ? 130 : undefined}
            step={field === "age" ? 1 : undefined}
            autoComplete={field === "name" ? "name" : "off"}
            placeholder={
              field === "age"
                ? "Optional"
                : field === "name"
                  ? "What should IRA call you?"
                  : "Your role or work"
            }
          />
        )}
      </Field>
    );
  };

  return (
    <div className="personal-memory-panel">
      <div className="connection-heading">
        <div>
          <h2 id="profile-title" className="text-[20px] font-semibold">
            Your Profile
          </h2>
          <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
            A little about you, so IRA can make the conversation yours.
          </p>
        </div>
        <span className="connection-provider-icon">
          <UserRound size={19} />
        </span>
      </div>
      {!memory.available && (
        <p className="connection-notice">
          Open IRA in the desktop app to edit your profile on this device.
        </p>
      )}
      {memory.profileLoading && (
        <p role="status" className="connection-notice">
          <LoaderCircle size={15} className="connection-spinner" />
          Loading your profile…
        </p>
      )}
      {memory.available && memory.profileError && (
        <div className="connection-feedback is-error">
          <p role="alert">{memory.profileError}</p>
          <button
            type="button"
            className="connection-retry"
            onClick={() => {
              void memory.refreshProfile();
            }}
          >
            Try again
          </button>
        </div>
      )}
      <form
        ref={form}
        aria-labelledby="profile-title"
        onSubmit={save}
        noValidate
      >
        <fieldset
          className="personal-memory-card profile-grid"
          disabled={disabled}
          aria-busy={memory.savingProfile}
        >
          <legend className="sr-only">Profile details</legend>
          {input(
            "name",
            "Name",
            "All profile fields are optional. Blank fields stay empty.",
            120,
          )}
          {input(
            "age",
            "Age (optional)",
            "A saved age from 0 to 130; IRA will not infer or advance it.",
          )}
          <div className="profile-wide">
            {input("occupation", "Occupation", undefined, 200)}
          </div>
          <div className="profile-wide">
            {input(
              "preferences",
              "Preferences",
              "Share response preferences, interests, or other useful context.",
              4000,
            )}
          </div>
        </fieldset>
        <Feedback error={error} success={success} />
        {conflict && (
          <button
            type="button"
            className="memory-secondary-button"
            disabled={memory.savingProfile || !memory.profile}
            onClick={() => {
              if (memory.profile) {
                acceptProfile(memory.profile);
                setSuccess(null);
              }
            }}
          >
            Reload saved profile
          </button>
        )}
        <div className="connection-save-row">
          <p className="connection-privacy-note">
            <ShieldCheck size={15} />
            <span>
              Saved on this device. Changes reach IRA at the next quiet moment.
            </span>
          </p>
          <div className="memory-action-buttons">
            <button
              type="button"
              className="memory-secondary-button"
              disabled={disabled}
              onClick={() => {
                setConfirmReset(true);
                setSuccess(null);
              }}
            >
              Reset profile
            </button>
            <button
              type="submit"
              className="connection-save-button"
              disabled={disabled}
            >
              {memory.savingProfile ? (
                <>
                  <LoaderCircle size={15} className="connection-spinner" />
                  Saving…
                </>
              ) : (
                <>
                  <Check size={15} />
                  Save profile
                </>
              )}
            </button>
          </div>
        </div>
      </form>
      {confirmReset && (
        <div
          className="memory-confirmation"
          role="group"
          aria-label="Reset profile confirmation"
        >
          <p>
            Reset your name, age, occupation, and preferences? Conversation
            memory is managed separately.
          </p>
          <div className="memory-action-buttons">
            <button
              type="button"
              className="memory-secondary-button"
              disabled={memory.savingProfile}
              onClick={() => setConfirmReset(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="memory-danger-button"
              disabled={memory.savingProfile}
              onClick={() => {
                void reset();
              }}
            >
              Confirm reset profile
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

type SettingsDraft = {
  enabled: boolean;
  context_budget: string;
  summary_trigger: string;
  recent_turns: string;
  summarizer_model: string;
};
const defaultDraft: SettingsDraft = {
  enabled: true,
  context_budget: "16000",
  summary_trigger: "12000",
  recent_turns: "12",
  summarizer_model: "gemini-3.8-flash",
};
function settingsDraft(settings: MemorySettings): SettingsDraft {
  return {
    enabled: settings.enabled,
    context_budget: String(settings.context_budget),
    summary_trigger: String(settings.summary_trigger),
    recent_turns: String(settings.recent_turns),
    summarizer_model: settings.summarizer_model,
  };
}
const statusDescriptions = {
  collecting: [
    "Collecting",
    "Keeping conversation turns for future summaries.",
  ],
  summarizing: [
    "Summarizing",
    "Preparing a summary while you continue the conversation.",
  ],
  waiting: [
    "Waiting to compact",
    "The summary is saved. Waiting for a quiet moment to apply it.",
  ],
  waiting_to_compact: [
    "Waiting to compact",
    "The summary is saved. Waiting for a quiet moment to apply it.",
  ],
  compacting: [
    "Compacting",
    "Applying the saved summary and reconnecting the voice context.",
  ],
  restored: [
    "Context restored",
    "Local context is updated. Waiting for the provider to respond.",
  ],
  ready: [
    "Ready",
    "Current provider activity confirmed after context restoration.",
  ],
  disabled: [
    "Disabled",
    "Automatic summaries and cross-session recall are off.",
  ],
  error: [
    "Needs attention",
    "Memory could not finish updating. Saved conversation data is retained.",
  ],
} as const;

export function MemorySettingsPanel({
  memory,
}: {
  memory: PersonalMemoryState;
}) {
  const [draft, setDraft] = useState<SettingsDraft>(defaultDraft);
  const [revision, setRevision] = useState<number | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof SettingsDraft, string>>
  >({});
  const [confirmClear, setConfirmClear] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const dirty = useRef(false);
  const advanced = useRef<HTMLDetailsElement>(null);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (memory.settings && !dirty.current) {
      setDraft(settingsDraft(memory.settings));
      setRevision(memory.settings.revision);
    }
  }, [memory.settings]);

  function change(field: keyof SettingsDraft, value: string | boolean) {
    dirty.current = true;
    setDraft((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setError(null);
    setSuccess(null);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !memory.available ||
      memory.memoryLoading ||
      memory.savingSettings ||
      !memory.settings
    )
      return;
    const budget = Number(draft.context_budget);
    const trigger = Number(draft.summary_trigger);
    const recent = Number(draft.recent_turns);
    const model = draft.summarizer_model.trim();
    const errors: Partial<Record<keyof SettingsDraft, string>> = {};
    if (!Number.isInteger(budget) || budget < 4096 || budget > 128000)
      errors.context_budget =
        "Choose a whole-number application budget from 4,096 to 128,000 tokens.";
    if (!Number.isInteger(trigger) || trigger < 1024 || trigger > budget - 2048)
      errors.summary_trigger =
        "Start at 1,024 tokens or more, leaving at least 2,048 tokens below the budget.";
    if (!Number.isInteger(recent) || recent < 4 || recent > 40)
      errors.recent_turns = "Keep 4 to 40 recent turns.";
    if (!model || model.length > 120 || !/^[\x21-\x7e]+$/.test(model))
      errors.summarizer_model =
        "Enter a model identifier within 120 printable characters, without spaces.";
    setFieldErrors(errors);
    setSuccess(null);
    const first = Object.keys(errors)[0];
    if (first) {
      setError(errors[first as keyof SettingsDraft]!);
      if (advanced.current) advanced.current.open = true;
      const element = form.current?.elements.namedItem(first);
      if (element instanceof HTMLElement) element.focus();
      return;
    }
    setError(null);
    const result = await memory.saveSettings(
      {
        enabled: draft.enabled,
        context_budget: budget,
        summary_trigger: trigger,
        recent_turns: recent,
        summarizer_model: model,
      },
      revision,
    );
    if ("error" in result) {
      setConflict(Boolean(result.conflict));
      setError(
        result.conflict
          ? `${result.error} Your edits are kept. Reload the saved settings to review the latest changes.`
          : result.error,
      );
    } else {
      dirty.current = false;
      setDraft(settingsDraft(result));
      setRevision(result.revision);
      setConflict(false);
      setSuccess(
        "Memory settings saved. IRA will apply them at the next quiet moment.",
      );
    }
  }

  async function deleteMemory(id: string) {
    setActionError(null);
    setActionSuccess(null);
    const result = await memory.deleteMemory(id);
    if ("error" in result) setActionError(result.error);
    else setActionSuccess("Memory deleted.");
  }
  async function clear() {
    setActionError(null);
    setActionSuccess(null);
    const result = await memory.clearMemory();
    if ("error" in result) setActionError(result.error);
    else {
      setConfirmClear(false);
      setActionSuccess("Conversation memory cleared. Your profile is kept.");
    }
  }

  const disabled =
    !memory.available ||
    memory.memoryLoading ||
    memory.savingSettings ||
    !memory.settings ||
    revision === undefined;
  const state =
    memory.settings?.enabled === false
      ? "disabled"
      : memory.memoryError
        ? undefined
        : memory.memoryStatus?.status?.state;
  const description = state && Object.hasOwn(statusDescriptions, state)
    ? statusDescriptions[state]
    : [
        "Session status unavailable",
        "A saved summary does not confirm a live provider connection.",
      ];
  const checkpoint = memory.memoryStatus?.checkpoint;
  function advancedField(
    field: Exclude<keyof SettingsDraft, "enabled">,
    label: string,
    help: string,
    min?: number,
    max?: number,
  ) {
    const id = `memory-${field}`;
    return (
      <Field id={id} label={label} help={help} error={fieldErrors[field]}>
        <input
          id={id}
          name={field}
          type={field === "summarizer_model" ? "text" : "number"}
          value={draft[field]}
          min={min}
          max={max}
          step={field === "summarizer_model" ? undefined : 1}
          maxLength={field === "summarizer_model" ? 120 : undefined}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => change(field, event.target.value)}
          aria-invalid={Boolean(fieldErrors[field])}
          aria-describedby={`${id}-help${fieldErrors[field] ? ` ${id}-error` : ""}`}
        />
      </Field>
    );
  }

  return (
    <div className="personal-memory-panel">
      <div className="connection-heading">
        <div>
          <h2 id="memory-title" className="text-[20px] font-semibold">
            Memory and Context
          </h2>
          <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
            Keep useful details through long conversations and future sessions.
          </p>
        </div>
        <span className="connection-provider-icon">
          <Database size={19} />
        </span>
      </div>
      {!memory.available && (
        <p className="connection-notice">
          Open IRA in the desktop app to inspect memory and manage automatic
          summaries.
        </p>
      )}
      {memory.memoryLoading && (
        <p role="status" className="connection-notice">
          <LoaderCircle size={15} className="connection-spinner" />
          Loading memory settings…
        </p>
      )}
      {memory.available && memory.memoryError && (
        <div className="connection-feedback is-error">
          <p role="alert">{memory.memoryError}</p>
          <button
            type="button"
            className="connection-retry"
            onClick={() => {
              void memory.refreshStatus();
            }}
          >
            Try again
          </button>
        </div>
      )}
      <form
        ref={form}
        aria-labelledby="memory-title"
        onSubmit={save}
        noValidate
      >
        <fieldset
          className="personal-memory-card memory-settings-fields"
          disabled={disabled}
          aria-busy={memory.savingSettings}
        >
          <legend className="sr-only">Automatic memory settings</legend>
          <div className="memory-toggle-row">
            <div>
              <label htmlFor="memory-enabled">Automatic memory</label>
              <p>
                Summarize conversations and recall useful context across
                sessions.
              </p>
            </div>
            <input
              id="memory-enabled"
              type="checkbox"
              className="memory-toggle"
              checked={draft.enabled}
              onChange={(event) => change("enabled", event.target.checked)}
            />
          </div>
          <p className="memory-key-note">
            Summarization uses your configured Google API key.
          </p>
          <details ref={advanced} className="memory-advanced">
            <summary>Advanced context settings</summary>
            <p className="connection-field-help">
              This is IRA’s application context budget. The model’s full
              capacity has not been verified.
            </p>
            <div className="memory-advanced-grid">
              {advancedField(
                "context_budget",
                "Application context budget (tokens)",
                "4,096–128,000 tokens for IRA’s active context.",
                4096,
                128000,
              )}
              {advancedField(
                "summary_trigger",
                "Summary trigger (tokens)",
                "Prepare a summary before reaching the budget; reserve at least 2,048 tokens.",
                1024,
              )}
              {advancedField(
                "recent_turns",
                "Recent turns to retain",
                "Keep 4–40 recent turns alongside the rolling summary.",
                4,
                40,
              )}
              {advancedField(
                "summarizer_model",
                "Summarizer model",
                "A text Gemini model available to your configured key.",
              )}
            </div>
          </details>
        </fieldset>
        <Feedback error={error} success={success} />
        {conflict && (
          <button
            type="button"
            className="memory-secondary-button"
            disabled={memory.savingSettings || !memory.settings}
            onClick={() => {
              if (memory.settings) {
                dirty.current = false;
                setDraft(settingsDraft(memory.settings));
                setRevision(memory.settings.revision);
                setFieldErrors({});
                setError(null);
                setConflict(false);
              }
            }}
          >
            Reload saved memory settings
          </button>
        )}
        <div className="memory-settings-save">
          <button
            type="submit"
            className="connection-save-button"
            disabled={disabled}
          >
            {memory.savingSettings ? (
              <>
                <LoaderCircle size={15} className="connection-spinner" />
                Saving…
              </>
            ) : (
              <>
                <Check size={15} />
                Save memory settings
              </>
            )}
          </button>
        </div>
      </form>
      <div
        className={`memory-context-status ${state === "error" ? "is-error" : ""}`}
        role="status"
        aria-label="Context memory status"
      >
        <span
          className={`memory-status-dot ${state === "ready" ? "is-ready" : ""}`}
          aria-hidden="true"
        />
        <div>
          <strong>{description[0]}</strong>
          <p>{description[1]}</p>
          {memory.memoryStatus?.status?.last_error && (
            <p className="connection-field-error">
              {memory.memoryStatus.status.last_error} Check your connection keys
              or summarizer model; IRA will retry.
            </p>
          )}
        </div>
        <button
          type="button"
          className="memory-secondary-button"
          disabled={!memory.available || memory.memoryLoading}
          onClick={() => {
            void memory.refreshStatus();
          }}
        >
          Refresh status
        </button>
      </div>
      <section
        className="personal-memory-card memory-summary"
        aria-labelledby="memory-summary-title"
      >
        <div className="memory-card-title">
          <h3 id="memory-summary-title">Last completed summary</h3>
          {checkpoint && (
            <time dateTime={checkpoint.created_at}>
              {new Date(checkpoint.created_at).toLocaleString()}
            </time>
          )}
        </div>
        {checkpoint ? (
          <>
            <p className="memory-summary-overview">
              {checkpoint.summary.overview || "Summary saved."}
            </p>
            {(
              [
                ["Facts", checkpoint.summary.facts],
                ["Decisions", checkpoint.summary.decisions],
                ["Open threads", checkpoint.summary.open_threads],
                ["Outcomes", checkpoint.summary.outcomes],
                ["Questions", checkpoint.summary.questions],
              ] as const
            ).map(
              ([title, entries]) =>
                entries.length > 0 && (
                  <div key={title} className="memory-summary-section">
                    <h4>{title}</h4>
                    <ul>
                      {entries.map((entry, index) => (
                        <li key={index}>{entry.content}</li>
                      ))}
                    </ul>
                  </div>
                ),
            )}
          </>
        ) : (
          <p className="memory-empty">
            No completed summary yet. New summaries appear here after a
            conversation has enough context.
          </p>
        )}
      </section>
      <section
        className="personal-memory-card memory-inspector"
        aria-labelledby="memory-inspect-title"
      >
        <div className="memory-card-title">
          <div>
            <h3 id="memory-inspect-title">Remembered details</h3>
            <p>
              Inspect saved facts and conversation memory, and remove what you
              no longer want IRA to recall.
            </p>
          </div>
          <button
            type="button"
            className="memory-secondary-button"
            disabled={!memory.available || memory.changingMemory}
            onClick={() => {
              setConfirmClear(true);
              setActionSuccess(null);
            }}
          >
            Clear conversation memory
          </button>
        </div>
        <div className="memory-search">
          <label htmlFor="memory-search" className="sr-only">
            Search memories
          </label>
          <Search size={15} aria-hidden="true" />
          <input
            id="memory-search"
            type="search"
            placeholder="Search remembered details"
            value={memory.query}
            onChange={(event) => memory.setQuery(event.target.value)}
            disabled={!memory.available}
          />
          <button
            type="button"
            className="memory-secondary-button"
            disabled={!memory.available || memory.listLoading}
            onClick={() => {
              void memory.refreshList();
            }}
          >
            Refresh
          </button>
        </div>
        {memory.listLoading && (
          <p className="connection-field-help" role="status">
            Loading remembered details…
          </p>
        )}
        {memory.listError && (
          <div className="connection-feedback is-error">
            <p role="alert">{memory.listError}</p>
            <button
              type="button"
              className="connection-retry"
              onClick={() => {
                void memory.refreshList();
              }}
            >
              Try again
            </button>
          </div>
        )}
        {!memory.listLoading &&
          !memory.listError &&
          memory.memories.length === 0 && (
            <p className="memory-empty">
              {memory.query
                ? "No memories match your search."
                : "No remembered details yet."}
            </p>
          )}
        <ul className="memory-records">
          {memory.memories.map((record) => (
            <li key={record.id}>
              <div>
                <p>{record.content}</p>
                <span className="memory-record-meta">
                  {record.kind} · {record.source}
                  {record.created_at && (
                    <>
                      {" "}
                      ·{" "}
                      <time dateTime={record.created_at}>
                        {new Date(record.created_at).toLocaleDateString()}
                      </time>
                    </>
                  )}
                </span>
              </div>
              <button
                type="button"
                className="memory-delete-button"
                aria-label={`Delete memory: ${record.content}`}
                disabled={memory.changingMemory}
                onClick={() => {
                  void deleteMemory(record.id);
                }}
              >
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
        <Feedback error={actionError} success={actionSuccess} />
        {confirmClear && (
          <div
            className="memory-confirmation"
            role="group"
            aria-label="Clear conversation memory confirmation"
          >
            <p>
              Clear saved conversation history, summaries, and remembered facts?
              Your profile stays saved.
            </p>
            <div className="memory-action-buttons">
              <button
                type="button"
                className="memory-secondary-button"
                disabled={memory.changingMemory}
                onClick={() => setConfirmClear(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="memory-danger-button"
                disabled={memory.changingMemory}
                onClick={() => {
                  void clear();
                }}
              >
                Confirm clear conversation memory
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
