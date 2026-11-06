/**
 * Types crossing the main/renderer boundary.
 *
 * Imported by both builds so the IPC contract can't drift. Duplicating these
 * in the renderer would mean a field rename in main compiles cleanly and
 * fails at runtime -- the exact bug the boundary exists to prevent.
 */

/** One snapshot of the host, shaped for the four rings in System Status. */
export type SystemStats = {
  /** Percent 0-100. */
  cpu: number;
  /** Percent of physical memory in use, 0-100. */
  memory: number;
  /** Percent used of the largest mounted volume, 0-100. */
  storage: number;
  battery: {
    percent: number;
    charging: boolean;
    /** False on a desktop PC, so the UI can show a plug instead of 0%. */
    hasBattery: boolean;
  };
  /** ISO-8601 timestamp of the read, for staleness display. */
  at: string;
};

/** Lifecycle of a supervised Python child process. */
export type ProcessState =
  | "starting"
  /** Spawned, but not yet confirmed able to do its job. */
  | "running"
  /** Printed its ready line -- for the agent, registered with LiveKit. */
  | "ready"
  | "restarting"
  | "failed"
  | "stopped";

export type ProcessStatus = {
  name: string;
  state: ProcessState;
  /** Populated when state is "failed", and shown to the user verbatim. */
  reason?: string;
  restarts: number;
};

/**
 * A minted room token, or why there isn't one.
 *
 * A result type rather than a thrown error: "the bridge hasn't finished
 * starting" is the normal case during launch, not an exception.
 */
export type TokenResult =
  | { url: string; room: string; token: string }
  | { error: string };

/** Configuration preflight. Booleans only -- never the values themselves. */
export type HealthResult = {
  bridge: string;
  config?: Record<string, boolean>;
  error?: string;
};

/** Key values only travel from the settings form to the desktop main process. */
export type ConnectionInput = {
  googleApiKey: string;
  livekitUrl: string;
  livekitApiKey: string;
  livekitApiSecret: string;
};

/** Saved secrets are represented by presence flags, never sent back to the UI. */
export type ConnectionSettings = {
  configured: boolean;
  livekitUrl: string;
  hasGoogleApiKey: boolean;
  hasLivekitApiKey: boolean;
  hasLivekitApiSecret: boolean;
  error?: string;
};

export type ConnectionSaveResult =
  | { settings: ConnectionSettings }
  | { error: string; field?: keyof ConnectionInput };

export type ConnectionApi = {
  get(): Promise<ConnectionSettings>;
  /** Blank key fields retain previously saved secrets. Validates before saving. */
  save(input: ConnectionInput): Promise<ConnectionSaveResult>;
  onChanged(listener: () => void): () => void;
};

export type UserProfile = {
  name: string;
  age: number | null;
  occupation: string;
  preferences: string;
  revision: number;
  updated_at: string | null;
};
export type UserProfileInput = Pick<UserProfile, "name" | "age" | "occupation" | "preferences">;
export type MemorySettings = {
  enabled: boolean;
  context_budget: number;
  summary_trigger: number;
  recent_turns: number;
  summarizer_model: string;
  revision: number;
};
export type MemorySummaryEntry = { content: string; source_item_ids: string[] };
export type ConversationSummary = {
  overview: string;
  facts: MemorySummaryEntry[];
  decisions: MemorySummaryEntry[];
  open_threads: MemorySummaryEntry[];
  outcomes: MemorySummaryEntry[];
  questions: MemorySummaryEntry[];
};
export type ContextCheckpoint = {
  id: string;
  conversation_id: string;
  through_seq: number;
  summary: ConversationSummary;
  memory_revision: number;
  created_at: string;
};
export type MemoryRecord = { id: string; kind: string; content: string; created_at: string; source: string };
export type ContextMemoryStatus = {
  state: "collecting" | "summarizing" | "waiting" | "waiting_to_compact" | "compacting" | "restored" | "ready" | "disabled" | "error";
  input_tokens?: number;
  last_error?: string | null;
  updated_at?: string;
};
export type PersonalMemoryStatus = {
  settings: MemorySettings;
  memory_revision: number;
  checkpoint: ContextCheckpoint | null;
  status?: ContextMemoryStatus | null;
};
export type PersonalMemoryResult<T> = T | { error: string; conflict?: boolean };
export type PersonalMemoryApi = {
  profile(): Promise<PersonalMemoryResult<UserProfile>>;
  saveProfile(input: UserProfileInput & { expected_revision?: number }): Promise<PersonalMemoryResult<UserProfile>>;
  resetProfile(): Promise<PersonalMemoryResult<UserProfile>>;
  settings(): Promise<PersonalMemoryResult<MemorySettings>>;
  saveSettings(input: Partial<Omit<MemorySettings, "revision">> & { expected_revision?: number }): Promise<PersonalMemoryResult<MemorySettings>>;
  list(query?: string, limit?: number): Promise<PersonalMemoryResult<{ memories: MemoryRecord[] }>>;
  status(): Promise<PersonalMemoryResult<PersonalMemoryStatus>>;
  deleteMemory(id: string): Promise<PersonalMemoryResult<{ deleted: boolean }>>;
  clearMemory(): Promise<PersonalMemoryResult<{ cleared: boolean }>>;
  onChanged(listener: () => void): () => void;
};

/** Both supervised processes, as the session controller sees them. */
export type ProcessSnapshot = {
  /**
   * False when IRA runs outside the dashboard (IRA_EXTERNAL_AGENT=1), e.g. in
   * a terminal so her logs stay where the boss can read them. The dashboard
   * then connects without waiting for a registration it will never observe.
   */
  supervised: boolean;
  /** A fresh desktop profile must enter its own keys before creating a room. */
  configurationRequired?: boolean;
  agent: ProcessStatus | null;
  bridge: ProcessStatus | null;
};

/** Stored task JSON is identical in Python, main and the renderer. */
export type Task = {
  id: string;
  title: string;
  due_at: string | null;
  done: boolean;
  created_at: string;
};

export type TaskInput = { title: string; due_at?: string | null };
export type TaskResult<T> = T | { error: string };
export type TasksApi = {
  list(limit?: number): Promise<TaskResult<{ tasks: Task[] }>>;
  create(input: TaskInput): Promise<TaskResult<{ task: Task }>>;
  update(id: string, patch: { done: boolean }): Promise<TaskResult<{ task: Task }>>;
  delete(id: string): Promise<TaskResult<{ deleted: boolean }>>;
};

/** Notifications carry no records; consumers refetch from the bridge. */
export type IraEvent = { type: "tasks.changed" | "skills.changed" };

export type SkillSummary = {
  slug: string;
  name: string;
  description: string;
  version: number;
  source: string;
  state: string;
  pinned: boolean;
  archived: boolean;
  use_count: number;
  failure_count: number;
  issues?: string[];
  executable?: boolean;
};

export type SkillDetail = SkillSummary & { markdown: string; versions: number[]; selected_version?: number; diff?: string };
export type SkillSuggestion = { slug: string; action: string; reason: string };
export type SkillRun = {
  run_id: string;
  skill_slug: string;
  skill_name: string;
  skill_version: number;
  status: string;
  completed_steps: number;
  total_steps: number;
  failed_step: number | null;
  message: string;
  question?: string;
  question_id?: string;
  input_name?: string;
  verified: boolean;
  started_at: number;
  finished_at: number | null;
  steps: Array<{step_index: number; tool: string; outcome: string; message: string; evidence: string; check_evidence?: string}>;
};

export type SkillsApi = {
  list(includeArchived?: boolean): Promise<TaskResult<{ skills: SkillSummary[] }>>;
  detail(slug: string, version?: number): Promise<TaskResult<{ skill: SkillDetail }>>;
  history(limit?: number): Promise<TaskResult<{ runs: SkillRun[] }>>;
  suggestions(): Promise<TaskResult<{ suggestions: SkillSuggestion[] }>>;
  pin(slug: string, pinned: boolean): Promise<TaskResult<{ skill: SkillSummary }>>;
  archive(slug: string): Promise<TaskResult<{ archived: boolean }>>;
  restore(slug: string): Promise<TaskResult<{ skill: SkillSummary }>>;
};
