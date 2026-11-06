import { ConnectionState, DataPacket_Kind, ParticipantKind, RoomEvent } from "livekit-client";

import type {
  HealthResult,
  ProcessSnapshot,
  ProcessStatus,
  TokenResult,
  IraEvent,
} from "@shared/types";

import type { AgentLifecycle } from "./orbState";
import { EMPTY_TRACKER, trackStatus, type StatusTracker } from "./statusTracker";
import { clearFinishedExecution, expireExecution, interruptExecution, trackExecution, type ExecutionActivity, type ExecutionEvent } from "./execution";

/**
 * Owns the connection between the dashboard and IRA.
 *
 * Kept out of React on purpose. The rules here -- when it is safe to create a
 * room, when to retry, when IRA has gone and a fresh room is needed -- are the
 * part of the voice spine that fails silently, and as a plain class they can
 * be driven against a fake room under fake timers. The hook that wraps this
 * is a thin adapter.
 *
 * The one rule that shapes everything else: **a room's dispatch is created
 * when the room is.** IRA is on explicit dispatch, and the token names her,
 * so the moment the dashboard joins is the moment LiveKit looks for her
 * worker. Join before that worker has registered and the dispatch has nowhere
 * to go; she never arrives. So the first connection waits for registration,
 * and every recovery creates a fresh room rather than waiting in a dead one.
 */

/** LiveKit publishes the agent's lifecycle on this participant attribute. */
export const AGENT_STATE_ATTRIBUTE = "lk.agent.state";
/** Must match STATUS_TOPIC in src/agent.py. */
export const STATUS_TOPIC = "ira.status";
export const EVENTS_TOPIC = "ira.events";
/** The Agents SDK's chat topic. Its handler turns text into a user turn. */
export const CHAT_TOPIC = "lk.chat";

export const JOIN_TIMEOUT_MESSAGE =
  "IRA hasn't joined yet. Her worker may have missed the dispatch -- check the agent log, or restart her.";
export const SPEAK_TIMEOUT_MESSAGE =
  "IRA joined but hasn't spoken. Check GOOGLE_API_KEY and GEMINI_LIVE_MODEL in the agent log.";

export type ParticipantLike = {
  identity: string;
  kind?: ParticipantKind;
  attributes?: Readonly<Record<string, string>>;
};

/**
 * The slice of `livekit-client`'s Room this controller uses.
 *
 * Narrow on purpose: it is what the tests fake, and everything not listed
 * here is something the controller has promised not to depend on. The real
 * Room is cast to this once, at the boundary in the hook.
 */
export type RoomLike = {
  state: ConnectionState;
  remoteParticipants: ReadonlyMap<string, ParticipantLike>;
  localParticipant: {
    isSpeaking: boolean;
    setMicrophoneEnabled(enabled: boolean): Promise<unknown>;
    sendText(text: string, options: { topic: string }): Promise<unknown>;
  };
  connect(url: string, token: string): Promise<void>;
  disconnect(): Promise<void>;
  // `never[]` so a listener with any specific signature is accepted.
  on(event: string, listener: (...args: never[]) => void): unknown;
  off(event: string, listener: (...args: never[]) => void): unknown;
};

export type { ProcessSnapshot };

export type SessionBridge = {
  token(): Promise<TokenResult>;
  health(): Promise<HealthResult>;
  processes(): Promise<ProcessSnapshot>;
  onProcess(listener: (status: ProcessStatus) => void): () => void;
};

export type ControllerOptions = {
  bridge: SessionBridge;
  createRoom: () => RoomLike;
  /** Her audio track, for playback and the waveform. */
  onAgentAudio?: (track: unknown) => void;
  /** Our microphone track, for the waveform while the boss speaks. */
  onMicTrack?: (track: unknown) => void;
  /** Connect without a ready signal after this long. See `start`. */
  readyTimeoutMs?: number;
  joinTimeoutMs?: number;
  speakTimeoutMs?: number;
  /** How long she may be gone from a room before we move to a fresh one. */
  rejoinGraceMs?: number;
  retryMs?: readonly number[];
  now?: () => number;
};

export type SessionSnapshot = {
  connection: "disconnected" | "connecting" | "connected";
  agentPresent: boolean;
  agentState: AgentLifecycle | undefined;
  userSpeaking: boolean;
  tracker: StatusTracker;
  activities: ExecutionActivity[];
  micEnabled: boolean;
  error: string | null;
};

export const INITIAL_SNAPSHOT: SessionSnapshot = {
  connection: "disconnected",
  agentPresent: false,
  agentState: undefined,
  userSpeaking: false,
  tracker: EMPTY_TRACKER,
  activities: [],
  micEnabled: false,
  error: null,
};

type Timer = ReturnType<typeof setTimeout>;
type Handler = (...args: never[]) => void;
const STATUS_STATES = new Set(["DELEGATING", "RUNNING", "VERIFYING", "DONE", "ERROR", "CANCELLED", "LISTENING", "RECOGNIZING", "GENERATING", "SPEAKING"]);

/** The agent participant, if she has joined. */
export function findAgent(room: RoomLike): ParticipantLike | undefined {
  for (const participant of room.remoteParticipants.values()) {
    if (participant.kind === ParticipantKind.AGENT) return participant;
    // A participant publishing an agent state is an agent, however its kind
    // was registered.
    if (participant.attributes?.[AGENT_STATE_ATTRIBUTE]) return participant;
  }
  return undefined;
}

function toConnection(state: ConnectionState): SessionSnapshot["connection"] {
  switch (state) {
    case ConnectionState.Connected:
      return "connected";
    case ConnectionState.Connecting:
    case ConnectionState.Reconnecting:
    case ConnectionState.SignalReconnecting:
      return "connecting";
    default:
      return "disconnected";
  }
}

export class SessionController {
  private state: SessionSnapshot = INITIAL_SNAPSHOT;
  private readonly listeners = new Set<(snapshot: SessionSnapshot) => void>();
  private readonly eventListeners = new Set<(event: IraEvent) => void>();

  private room: RoomLike | null = null;
  private handlers: Array<[string, Handler]> = [];
  private connecting = false;
  private disposed = false;

  private supervised = true;
  private configurationRequired = false;
  private agentProcess: ProcessStatus | null = null;
  private readyTimedOut = false;
  private heardAgent = false;

  private readonly timers = new Set<Timer>();
  private readonly pendingDelays = new Set<() => void>();
  private joinTimer: Timer | null = null;
  private speakTimer: Timer | null = null;
  private rejoinTimer: Timer | null = null;
  private unsubscribeProcess: (() => void) | null = null;
  private activityTimer: Timer | null = null;

  constructor(private readonly options: ControllerOptions) {}

  // -- public surface -------------------------------------------------------

  snapshot(): SessionSnapshot {
    return this.state;
  }

  subscribe(listener: (snapshot: SessionSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  subscribeEvents(listener: (event: IraEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => { this.eventListeners.delete(listener); };
  }

  /**
   * Begin. Connects once IRA's worker has registered.
   *
   * The registration signal is a log line from the SDK, which a future
   * version could reword. So after `readyTimeoutMs` without one, it connects
   * anyway: slow to connect beats unable to.
   */
  async start(): Promise<void> {
    this.unsubscribeProcess = this.options.bridge.onProcess((status) =>
      this.applyProcess(status),
    );

    let processes: ProcessSnapshot;
    try {
      processes = await this.options.bridge.processes();
    } catch (cause) {
      // Without this the rejection went unhandled and the orb sat on
      // CONNECTING forever. Nothing below can work without the shell, so say
      // which piece is missing and stop.
      if (!this.disposed) {
        this.set({
          connection: "disconnected",
          error: `The desktop shell isn't answering (${(cause as Error).message}). Restart the app.`,
        });
      }
      return;
    }
    if (this.disposed) return;

    this.supervised = processes.supervised;
    this.configurationRequired = processes.configurationRequired === true;
    if (processes.agent) this.applyProcess(processes.agent, false);

    if (this.configurationRequired) {
      this.set({
        connection: "disconnected",
        error: "Open Settings > Connection and Keys to save your Google and LiveKit credentials and connect IRA.",
      });
      return;
    }

    if (this.agentProcess?.state !== "failed") this.set({ connection: "connecting" });

    this.later(this.options.readyTimeoutMs ?? 45_000, () => {
      this.readyTimedOut = true;
      this.evaluate();
    });

    this.evaluate();
  }

  send(text: string): void {
    const room = this.room;
    // Dropped rather than queued: a command typed while offline and replayed
    // minutes later, once she connects, would act on a stale intention.
    if (!room || room.state !== ConnectionState.Connected) return;
    void room.localParticipant.sendText(text, { topic: CHAT_TOPIC }).catch(() => {
      this.set({ error: "Couldn't send that to IRA. Try again." });
    });
  }

  clearExecutionHistory(): void {
    this.set({ activities: clearFinishedExecution(this.state.activities) });
  }

  setMicrophone(enabled: boolean): void {
    const room = this.room;
    if (!room) return;
    this.set({ micEnabled: enabled });
    void room.localParticipant.setMicrophoneEnabled(enabled).catch(() => {
      // Put the switch back rather than show a lit mic that isn't recording.
      this.set({ micEnabled: !enabled });
    });
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribeProcess?.();
    this.unsubscribeProcess = null;

    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    // Let any retry loop waiting on a delay wake, see `disposed`, and exit.
    for (const resolve of this.pendingDelays) resolve();
    this.pendingDelays.clear();

    this.leaveRoom();
    this.listeners.clear();
    this.eventListeners.clear();
  }

  // -- process supervision --------------------------------------------------

  private applyProcess(status: ProcessStatus, react = true): void {
    if (status.name !== "agent") return;

    const wasReady = this.agentProcess?.state === "ready";
    this.agentProcess = status;
    if (!react || this.disposed) return;

    // Re-registered after a restart. Her old dispatch died with the old
    // process, so if she isn't in our room she isn't coming back to it.
    if (status.state === "ready" && !wasReady && this.room && !this.state.agentPresent) {
      this.moveToFreshRoom();
      return;
    }

    this.evaluate();
  }

  private agentReady(): boolean {
    if (!this.supervised) return true;
    return this.agentProcess?.state === "ready";
  }

  private evaluate(): void {
    if (this.disposed || this.configurationRequired || this.room || this.connecting) return;
    if (this.supervised && this.agentProcess?.state === "failed") return;
    if (!this.agentReady() && !this.readyTimedOut) return;
    void this.connect();
  }

  // -- connecting -----------------------------------------------------------

  private async connect(): Promise<void> {
    this.connecting = true;
    this.set({ connection: "connecting" });

    const retry = this.options.retryMs ?? [1_000, 2_000, 4_000, 8_000, 10_000];
    let failures = 0;
    const backoff = () => retry[Math.min(failures - 1, retry.length - 1)] ?? 10_000;

    while (!this.disposed) {
      const result = await this.options.bridge.token();
      if (this.disposed) break;

      if ("error" in result) {
        // A missing key fails identically forever: say which one and stop.
        const missing = await this.missingConfiguration();
        if (this.disposed) break;
        if (missing.length > 0) {
          this.connecting = false;
          this.set({
            connection: "disconnected",
            error: `Not configured: ${missing.join(", ")}. Add ${
              missing.length === 1 ? "it" : "them"
            } in Settings > Connection and Keys and save to reconnect IRA.`,
          });
          return;
        }

        failures += 1;
        // The first couple of failures are the bridge still booting, which is
        // normal at launch and not worth alarming anyone about.
        if (failures >= 3) {
          this.set({ error: `Can't reach IRA's bridge (${result.error}). Retrying.` });
        }
        await this.delay(backoff());
        continue;
      }

      const room = this.options.createRoom();
      this.bindRoom(room);
      try {
        await room.connect(result.url, result.token);
      } catch (cause) {
        this.unbindRoom(room);
        failures += 1;
        if (failures >= 2) {
          this.set({
            error: `Can't reach LiveKit (${(cause as Error).message}). Retrying.`,
          });
        }
        await this.delay(backoff());
        continue;
      }

      if (this.disposed) {
        this.unbindRoom(room);
        void room.disconnect();
        break;
      }

      this.room = room;
      this.connecting = false;
      this.heardAgent = false;
      this.set({ connection: "connected", error: null });
      this.syncAgent(room);
      this.armJoinWatchdog();

      try {
        await room.localParticipant.setMicrophoneEnabled(true);
        if (this.room === room) this.set({ micEnabled: true });
      } catch (cause) {
        if (this.room === room) {
          this.set({
            micEnabled: false,
            error: `Microphone unavailable (${(cause as Error).message}). Check Windows privacy settings for microphone access.`,
          });
        }
      }
      return;
    }

    this.connecting = false;
  }

  private async missingConfiguration(): Promise<string[]> {
    try {
      const health = await this.options.bridge.health();
      return Object.entries(health.config ?? {})
        .filter(([, present]) => !present)
        .map(([key]) => key.toUpperCase());
    } catch {
      return [];
    }
  }

  /** Leave the current room and start over in a new one. */
  private moveToFreshRoom(): void {
    this.leaveRoom();
    this.set({ connection: "connecting", agentPresent: false, agentState: undefined });
    this.evaluate();
  }

  private leaveRoom(): void {
    this.clearWatchdogs();
    this.interruptActivities();
    const room = this.room;
    this.room = null;
    if (!room) return;
    // Unbind first: the real Room emits Disconnected for our own disconnect,
    // and reacting to it would schedule a reconnect nobody asked for.
    this.unbindRoom(room);
    void room.disconnect();
  }

  // -- room events ----------------------------------------------------------

  private bindRoom(room: RoomLike): void {
    const mine = () => this.room === room || this.connecting;

    const sync = () => {
      if (this.room === room) this.syncAgent(room);
    };

    const handlers: Array<[string, Handler]> = [
      [
        RoomEvent.ConnectionStateChanged,
        ((state: ConnectionState) => {
          if (this.room === room) {
            if (state !== ConnectionState.Connected) this.interruptActivities("unavailable");
            this.set({ connection: toConnection(state) });
          }
        }) as Handler,
      ],
      [RoomEvent.ParticipantConnected, sync],
      [RoomEvent.ParticipantDisconnected, sync],
      [RoomEvent.ParticipantAttributesChanged, sync],
      [
        RoomEvent.ActiveSpeakersChanged,
        (() => {
          if (this.room === room) {
            this.set({ userSpeaking: room.localParticipant.isSpeaking });
          }
        }) as Handler,
      ],
      [
        RoomEvent.DataReceived,
        ((payload: Uint8Array, who?: ParticipantLike, kind?: DataPacket_Kind, topic?: string) => {
          if (topic === EVENTS_TOPIC) {
            // Agent state attributes are writable by ordinary participants.
            // The server-assigned kind and membership establish the sender.
            if (!mine() || this.disposed || kind !== DataPacket_Kind.RELIABLE || !who || who.kind !== ParticipantKind.AGENT || room.remoteParticipants.get(who.identity) !== who || payload.byteLength > 1024) return;
            try {
              const event: unknown = JSON.parse(new TextDecoder().decode(payload));
              if (!event || typeof event !== "object" || Array.isArray(event) || Object.keys(event).length !== 1 || !("type" in event) || (event.type !== "tasks.changed" && event.type !== "skills.changed")) return;
              for (const listener of this.eventListeners) listener({ type: event.type });
            } catch { /* Invalid data never triggers a bridge request. */ }
            return;
          }
          if (topic !== STATUS_TOPIC || !mine() || this.disposed || kind !== DataPacket_Kind.RELIABLE || !who || who.kind !== ParticipantKind.AGENT || room.remoteParticipants.get(who.identity) !== who || payload.byteLength > 4096) return;
          try {
            const value: unknown = JSON.parse(new TextDecoder().decode(payload));
            if (!value || typeof value !== "object" || Array.isArray(value)) return;
            const event = value as Record<string, unknown>;
            if (typeof event.state !== "string" || !STATUS_STATES.has(event.state) || (event.detail !== undefined && (typeof event.detail !== "string" || event.detail.length > 1024))) return;
            const at = (this.options.now ?? Date.now)();
            const detail = event.detail as string | undefined;
            const hasMetadata = ["call_id", "tool_name", "source"].some((field) => field in event);
            if (hasMetadata) {
              if (![event.run_id, event.call_id, event.tool_name].every((field) => typeof field === "string" && field.length > 0 && field.length <= 128) || (event.source !== "lifecycle" && event.source !== "tool" && event.source !== "skill")) return;
              if (["skill_run_id", "skill_slug", "outcome"].some((key) => event[key] !== undefined && (typeof event[key] !== "string" || !event[key] || (event[key] as string).length > 128))) return;
              if (["skill_version", "step_index", "step_count"].some((key) => event[key] !== undefined && (typeof event[key] !== "number" || !Number.isInteger(event[key]) || (event[key] as number) < 0 || (event[key] as number) > 1_000_000))) return;
              if (event.step_tool !== undefined && (typeof event.step_tool !== "string" || !event.step_tool.trim() || event.step_tool.length > 128)) return;
              if (event.step_outcome !== undefined && (typeof event.step_outcome !== "string" || !["running", "verifying", "waiting", "done", "error", "cancelled", "uncertain"].includes(event.step_outcome))) return;
              if ((event.step_index !== undefined || event.step_count !== undefined)
                && (typeof event.step_index !== "number" || typeof event.step_count !== "number" || event.step_index > event.step_count || event.step_count > 10_000)) return;
              const activities = trackExecution(this.state.activities, { ...event, at } as ExecutionEvent);
              // Only correlated execution drives tool state. Internal updates
              // must not duplicate calls or finish a different active action.
              if (activities === this.state.activities) return;
              const tracker = this.activityTracker(activities, event.state === "ERROR" ? at : this.state.tracker.errorAt);
              this.set({ activities, tracker });
              this.armActivityExpiry();
            } else if (this.state.activities.some((item) => item.phase === "running" || item.phase === "verifying")) {
              // Older/manual uncorrelated statuses cannot erase live calls.
              return;
            } else {
              this.set({ tracker: trackStatus(this.state.tracker, { state: event.state, detail, at }) });
            }
          } catch {
            // A malformed status message is not worth a broken orb.
          }
        }) as Handler,
      ],
      [
        RoomEvent.TrackSubscribed,
        ((track: { kind?: string }) => {
          if (track?.kind !== "audio" || this.room !== room) return;
          this.heardAgent = true;
          this.clearTimer("speak");
          if (this.state.error === SPEAK_TIMEOUT_MESSAGE) this.set({ error: null });
          this.options.onAgentAudio?.(track);
        }) as Handler,
      ],
      [
        RoomEvent.LocalTrackPublished,
        ((publication: { kind?: string; track?: unknown }) => {
          if (publication?.kind === "audio" && publication.track) {
            this.options.onMicTrack?.(publication.track);
          }
        }) as Handler,
      ],
      [
        RoomEvent.Disconnected,
        (() => {
          if (this.room !== room || this.disposed) return;
          // The server ended the room, or the network outlasted LiveKit's own
          // reconnection. Either way this room is finished; start a new one.
          this.leaveRoom();
          this.set({
            connection: "disconnected",
            agentPresent: false,
            agentState: undefined,
            micEnabled: false,
          });
          const retry = this.options.retryMs ?? [1_000];
          this.later(retry[0] ?? 1_000, () => this.evaluate());
        }) as Handler,
      ],
    ];

    for (const [event, handler] of handlers) room.on(event, handler);
    this.handlers = handlers;
  }

  private unbindRoom(room: RoomLike): void {
    for (const [event, handler] of this.handlers) room.off(event, handler);
    this.handlers = [];
  }

  private syncAgent(room: RoomLike): void {
    const agent = findAgent(room);
    const present = Boolean(agent);
    const wasPresent = this.state.agentPresent;

    this.set({
      agentPresent: present,
      agentState: agent?.attributes?.[AGENT_STATE_ATTRIBUTE] as AgentLifecycle | undefined,
    });

    if (present && !wasPresent) {
      this.clearTimer("join");
      this.clearTimer("rejoin");
      if (this.state.error === JOIN_TIMEOUT_MESSAGE) this.set({ error: null });
      if (!this.heardAgent) this.armSpeakWatchdog();
    }

    if (!present && wasPresent) {
      this.interruptActivities();
      // She left. Give her a moment -- a brief network blip looks the same --
      // then move to a fresh room if her worker is still up. A worker that
      // re-registers is handled sooner, in applyProcess.
      this.clearTimer("speak");
      this.rejoinTimer = this.later(this.options.rejoinGraceMs ?? 4_000, () => {
        this.rejoinTimer = null;
        if (this.room === room && !this.state.agentPresent && this.agentReady()) {
          this.moveToFreshRoom();
        }
      });
    }
  }

  // -- watchdogs ------------------------------------------------------------

  private armJoinWatchdog(): void {
    this.clearTimer("join");
    this.joinTimer = this.later(this.options.joinTimeoutMs ?? 20_000, () => {
      this.joinTimer = null;
      if (this.room && !this.state.agentPresent) this.set({ error: JOIN_TIMEOUT_MESSAGE });
    });
  }

  private armSpeakWatchdog(): void {
    this.clearTimer("speak");
    this.speakTimer = this.later(this.options.speakTimeoutMs ?? 15_000, () => {
      this.speakTimer = null;
      if (this.room && this.state.agentPresent && !this.heardAgent) {
        this.set({ error: SPEAK_TIMEOUT_MESSAGE });
      }
    });
  }

  private clearWatchdogs(): void {
    this.clearTimer("join");
    this.clearTimer("speak");
    this.clearTimer("rejoin");
  }

  private clearTimer(which: "join" | "speak" | "rejoin"): void {
    const key = `${which}Timer` as const;
    const timer = this[key];
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(timer);
    }
    this[key] = null;
  }

  // -- plumbing -------------------------------------------------------------

  private activityTracker(activities: ExecutionActivity[], errorAt = this.state.tracker.errorAt): StatusTracker {
    const active = activities.find((item) => item.phase === "running" || item.phase === "verifying");
    return { tool: active ? { name: active.toolName, lastUpdate: active.updatedAt } : null, errorAt };
  }

  private interruptActivities(phase: "cancelled" | "unavailable" = "cancelled"): void {
    if (this.activityTimer) { clearTimeout(this.activityTimer); this.timers.delete(this.activityTimer); this.activityTimer = null; }
    this.set({ activities: interruptExecution(this.state.activities, (this.options.now ?? Date.now)(), phase), tracker: EMPTY_TRACKER });
  }

  private armActivityExpiry(): void {
    if (this.activityTimer) { clearTimeout(this.activityTimer); this.timers.delete(this.activityTimer); this.activityTimer = null; }
    const active = this.state.activities.filter((item) => item.phase === "running" || item.phase === "verifying");
    if (active.length === 0) return;
    const deadline = Math.min(...active.map((item) => item.updatedAt + 30_000));
    this.activityTimer = this.later(Math.max(0, deadline - (this.options.now ?? Date.now)()), () => {
      this.activityTimer = null;
      const activities = expireExecution(this.state.activities, (this.options.now ?? Date.now)());
      this.set({ activities, tracker: this.activityTracker(activities) });
      this.armActivityExpiry();
    });
  }

  private later(ms: number, fn: () => void): Timer {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (!this.disposed) fn();
    }, ms);
    this.timers.add(timer);
    return timer;
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => {
      const done = () => {
        this.pendingDelays.delete(done);
        resolve();
      };
      this.pendingDelays.add(done);
      this.later(ms, done);
    });
  }

  private set(partial: Partial<SessionSnapshot>): void {
    if (this.disposed) return;
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) listener(this.state);
  }
}
