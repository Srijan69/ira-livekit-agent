import { ConnectionState, DataPacket_Kind, ParticipantKind, RoomEvent } from "livekit-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthResult, ProcessStatus, TokenResult } from "@shared/types";

import {
  JOIN_TIMEOUT_MESSAGE,
  SPEAK_TIMEOUT_MESSAGE,
  SessionController,
  type RoomLike,
  type SessionSnapshot,
} from "./sessionController";

/**
 * The connection lifecycle, against a fake room and a fake bridge.
 *
 * No network, ever -- the same rule the Python suite follows for browsers and
 * desktops. These are the paths that fail silently in a real app: a room
 * created before IRA's worker exists, a bridge that isn't up yet, an agent
 * that restarts mid-session. Each one used to look identical from the outside:
 * an orb saying CONNECTING forever.
 */

// Mirrors RoomLike's listener type exactly; `emit` widens it to call it.
type Listener = (...args: never[]) => void;

class FakeParticipant {
  constructor(
    readonly identity: string,
    readonly kind: ParticipantKind,
    public attributes: Record<string, string> = {},
  ) {}
}

class FakeRoom implements RoomLike {
  state: ConnectionState = ConnectionState.Disconnected;
  remoteParticipants = new Map<string, FakeParticipant>();
  failConnect = false;
  disconnected = false;
  connectCalls: Array<[string, string]> = [];
  readonly sent: Array<{ text: string; topic: string }> = [];
  private readonly listeners = new Map<string, Set<Listener>>();

  localParticipant = {
    isSpeaking: false,
    micEnabled: false,
    failMic: false,
    setMicrophoneEnabled: async (enabled: boolean) => {
      if (this.localParticipant.failMic) throw new Error("NotAllowedError");
      this.localParticipant.micEnabled = enabled;
    },
    sendText: async (text: string, options: { topic: string }) => {
      this.sent.push({ text, topic: options.topic });
    },
  };

  on(event: string, listener: Listener) {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(listener);
    return this;
  }

  off(event: string, listener: Listener) {
    this.listeners.get(event)?.delete(listener);
    return this;
  }

  listenerCount(): number {
    let total = 0;
    for (const set of this.listeners.values()) total += set.size;
    return total;
  }

  emit(event: string, ...args: unknown[]) {
    for (const listener of this.listeners.get(event) ?? []) {
      (listener as (...values: unknown[]) => void)(...args);
    }
  }

  async connect(url: string, token: string) {
    this.connectCalls.push([url, token]);
    if (this.failConnect) throw new Error("could not establish signal connection");
    this.state = ConnectionState.Connected;
    this.emit(RoomEvent.ConnectionStateChanged, ConnectionState.Connected);
  }

  async disconnect() {
    this.disconnected = true;
    this.state = ConnectionState.Disconnected;
  }

  // -- scripted events ------------------------------------------------------

  agentJoins(state = "listening") {
    const agent = new FakeParticipant("agent-1", ParticipantKind.AGENT, {
      "lk.agent.state": state,
    });
    this.remoteParticipants.set(agent.identity, agent);
    this.emit(RoomEvent.ParticipantConnected, agent);
  }

  agentLeaves() {
    const agent = this.remoteParticipants.get("agent-1");
    this.remoteParticipants.delete("agent-1");
    this.emit(RoomEvent.ParticipantDisconnected, agent);
  }

  agentBecomes(state: string) {
    const agent = this.remoteParticipants.get("agent-1")!;
    agent.attributes = { "lk.agent.state": state };
    this.emit(RoomEvent.ParticipantAttributesChanged, agent.attributes, agent);
  }

  status(state: string, detail?: string) {
    const payload = new TextEncoder().encode(JSON.stringify({ state, detail }));
    this.emit(
      RoomEvent.DataReceived,
      payload,
      this.remoteParticipants.get("agent-1"),
      DataPacket_Kind.RELIABLE,
      "ira.status",
    );
  }

  agentAudio() {
    this.emit(
      RoomEvent.TrackSubscribed,
      { kind: "audio" },
      {},
      this.remoteParticipants.get("agent-1"),
    );
  }

  serverDrops() {
    this.state = ConnectionState.Disconnected;
    this.emit(RoomEvent.Disconnected);
  }
}

const OK_TOKEN: TokenResult = {
  url: "wss://example.livekit.cloud",
  room: "ira-test",
  token: "jwt",
};

const ALL_CONFIGURED: HealthResult = {
  bridge: "ok",
  config: { livekit_url: true, api_key: true, api_secret: true, google_api_key: true },
};

const agent = (state: ProcessStatus["state"]): ProcessStatus => ({
  name: "agent",
  state,
  restarts: 0,
});

function setup(
  options: {
    agentState?: ProcessStatus["state"] | null;
    tokens?: TokenResult[];
    health?: HealthResult;
    configurationRequired?: boolean;
  } = {},
) {
  const rooms: FakeRoom[] = [];
  const tokenQueue = [...(options.tokens ?? [])];
  let processListener: ((status: ProcessStatus) => void) | undefined;
  let tokenCalls = 0;
  const audio: unknown[] = [];

  const agentState = options.agentState === undefined ? "ready" : options.agentState;

  const controller = new SessionController({
    bridge: {
      token: async () => {
        tokenCalls += 1;
        return tokenQueue.shift() ?? OK_TOKEN;
      },
      health: async () => options.health ?? ALL_CONFIGURED,
      processes: async () => ({
        supervised: agentState !== null,
        configurationRequired: options.configurationRequired,
        agent: agentState === null ? null : agent(agentState),
        bridge: { name: "bridge", state: "ready", restarts: 0 },
      }),
      onProcess: (listener) => {
        processListener = listener;
        return () => {
          processListener = undefined;
        };
      },
    },
    createRoom: () => {
      const room = new FakeRoom();
      rooms.push(room);
      return room;
    },
    onAgentAudio: (track) => audio.push(track),
  });

  const snapshots: SessionSnapshot[] = [];
  controller.subscribe((snapshot) => snapshots.push(snapshot));

  return {
    controller,
    rooms,
    audio,
    snap: () => controller.snapshot(),
    tokenCalls: () => tokenCalls,
    pushProcess: (status: ProcessStatus) => processListener?.(status),
    room: () => rooms[rooms.length - 1]!,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SessionController", () => {
  describe("waiting for IRA", () => {
    it("waits for the user's saved credentials without requesting a token or microphone", async () => {
      const t = setup({ configurationRequired: true });
      await t.controller.start();
      t.pushProcess(agent("ready"));
      await vi.advanceTimersByTimeAsync(90_000);

      expect(t.tokenCalls()).toBe(0);
      expect(t.rooms).toHaveLength(0);
      expect(t.snap().connection).toBe("disconnected");
      expect(t.snap().error).toContain("Connection and Keys");
    });

    it("does not create a room until her worker has registered", async () => {
      // A room created first gets its dispatch before any worker exists to
      // take it -- and nobody ever joins.
      const t = setup({ agentState: "running" });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(5_000);

      expect(t.tokenCalls()).toBe(0);
      expect(t.rooms).toHaveLength(0);

      t.pushProcess(agent("ready"));
      await vi.advanceTimersByTimeAsync(0);

      expect(t.tokenCalls()).toBe(1);
      expect(t.room().connectCalls).toEqual([[OK_TOKEN.url, OK_TOKEN.token]]);
      expect(t.snap().connection).toBe("connected");
    });

    it("connects anyway if the ready line never appears", async () => {
      // The ready signal is a log line from the SDK. If a future version
      // rewords it, the dashboard should be slow to connect, not unable to.
      const t = setup({ agentState: "running" });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(45_001);

      expect(t.tokenCalls()).toBe(1);
    });

    it("never connects while her process has failed", async () => {
      // The status banner explains a failed process; a room would only add a
      // second, more confusing symptom.
      const t = setup({ agentState: "failed" });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(60_000);

      expect(t.tokenCalls()).toBe(0);
    });

    it("connects straight away when the agent is not supervised", async () => {
      // IRA_EXTERNAL_AGENT: the boss runs her in a terminal to watch the logs.
      const t = setup({ agentState: null });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      expect(t.tokenCalls()).toBe(1);
    });
  });

  describe("retrying", () => {
    it("retries the token while the bridge is still starting", async () => {
      const t = setup({
        tokens: [
          { error: "bridge unreachable: ECONNREFUSED" },
          { error: "bridge unreachable: ECONNREFUSED" },
        ],
      });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(10_000);

      expect(t.tokenCalls()).toBe(3);
      expect(t.snap().connection).toBe("connected");
      expect(t.snap().error).toBeNull();
    });

    it("stops retrying and names what is missing from the configuration", async () => {
      // A missing key fails identically forever. Say which one, once.
      const t = setup({
        tokens: [{ error: "LIVEKIT_URL is not set" }],
        health: {
          bridge: "ok",
          config: {
            livekit_url: false,
            api_key: true,
            api_secret: true,
            google_api_key: false,
          },
        },
      });
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(30_000);

      expect(t.tokenCalls()).toBe(1);
      expect(t.snap().error).toContain("LIVEKIT_URL");
      expect(t.snap().error).toContain("GOOGLE_API_KEY");
      expect(t.snap().error).toContain("Connection and Keys");
      expect(t.snap().connection).toBe("disconnected");
    });

    it("retries a failed room connection with a fresh token", async () => {
      const t = setup();
      let attempt = 0;
      const original = t.controller["options"].createRoom;
      t.controller["options"].createRoom = () => {
        const room = original() as FakeRoom;
        attempt += 1;
        room.failConnect = attempt === 1;
        return room;
      };

      await t.controller.start();
      await vi.advanceTimersByTimeAsync(5_000);

      expect(t.rooms).toHaveLength(2);
      expect(t.rooms[0]!.listenerCount()).toBe(0);
      expect(t.snap().connection).toBe("connected");
    });

    it("reconnects with a fresh token when the server drops the room", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      t.room().serverDrops();
      await vi.advanceTimersByTimeAsync(5_000);

      expect(t.rooms).toHaveLength(2);
      expect(t.rooms[0]!.listenerCount()).toBe(0);
      expect(t.snap().connection).toBe("connected");
    });
  });

  describe("IRA in the room", () => {
    it("reports her presence and lifecycle from her attributes", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      expect(t.snap().agentPresent).toBe(false);

      t.room().agentJoins("listening");
      expect(t.snap().agentPresent).toBe(true);
      expect(t.snap().agentState).toBe("listening");

      t.room().agentBecomes("speaking");
      expect(t.snap().agentState).toBe("speaking");
    });

    it("moves to a fresh room when her worker re-registers after a restart", async () => {
      // Her old dispatch died with the old process. Staying put means waiting
      // in a room she will never come back to.
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      t.room().agentLeaves();
      t.pushProcess(agent("restarting"));
      t.pushProcess(agent("running"));
      t.pushProcess(agent("ready"));
      await vi.advanceTimersByTimeAsync(0);

      expect(t.rooms).toHaveLength(2);
      expect(t.rooms[0]!.disconnected).toBe(true);
      expect(t.tokenCalls()).toBe(2);
    });

    it("moves to a fresh room when she leaves but her worker stays up", async () => {
      // A crashed job with a healthy worker: no re-registration to react to.
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      t.room().agentLeaves();
      await vi.advanceTimersByTimeAsync(4_001);

      expect(t.rooms).toHaveLength(2);
    });

    it("stays put if she rejoins within the grace period", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      t.room().agentLeaves();
      await vi.advanceTimersByTimeAsync(1_000);
      t.room().agentJoins();
      await vi.advanceTimersByTimeAsync(10_000);

      expect(t.rooms).toHaveLength(1);
    });
  });

  describe("watchdogs", () => {
    it("says so when she hasn't joined, and clears it when she does", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(20_001);
      expect(t.snap().error).toBe(JOIN_TIMEOUT_MESSAGE);

      t.room().agentJoins();
      expect(t.snap().error).toBeNull();
    });

    it("says so when she joined but hasn't spoken", async () => {
      // The README documents the cause: a live model that rejects
      // generate_reply() joins, never greets, and logs nothing visible.
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      await vi.advanceTimersByTimeAsync(15_001);
      expect(t.snap().error).toBe(SPEAK_TIMEOUT_MESSAGE);

      t.room().agentAudio();
      expect(t.snap().error).toBeNull();
      expect(t.audio).toHaveLength(1);
    });

    it("stays quiet when she speaks in time", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      t.room().agentAudio();

      await vi.advanceTimersByTimeAsync(60_000);
      expect(t.snap().error).toBeNull();
    });
  });

  describe("talking to her", () => {
    it("turns the microphone on once connected", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      expect(t.room().localParticipant.micEnabled).toBe(true);
      expect(t.snap().micEnabled).toBe(true);
    });

    it("reports a refused microphone instead of showing a lit mic", async () => {
      const t = setup();
      const original = t.controller["options"].createRoom;
      t.controller["options"].createRoom = () => {
        const room = original() as FakeRoom;
        room.localParticipant.failMic = true;
        return room;
      };
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      expect(t.snap().micEnabled).toBe(false);
      expect(t.snap().error).toMatch(/microphone/i);
    });

    it("sends typed text on the lk.chat topic", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      t.controller.send("Open VS Code.");
      expect(t.room().sent).toEqual([{ text: "Open VS Code.", topic: "lk.chat" }]);
    });

    it("drops text when there is no room rather than queueing it", async () => {
      // A command typed while offline and replayed minutes later, once she
      // connects, would act on a stale intention.
      const t = setup({ agentState: "running" });
      await t.controller.start();
      t.controller.send("Open VS Code.");
      expect(t.rooms).toHaveLength(0);
    });
  });

  describe("status stream", () => {
    it("validates step telemetry and retains waiting skills until resumed or disconnected", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      t.room().agentAudio();
      const emit = (state: string, call = "skill-call", overrides = {}) => t.room().emit(RoomEvent.DataReceived,
        executionPacket(state, call, { tool_name: "run_skill", ...overrides }),
        t.room().remoteParticipants.get("agent-1"), DataPacket_Kind.RELIABLE, "ira.status");
      const skill = { source: "skill", skill_run_id: "skill-1", step_index: 1, step_count: 2, step_tool: "browser_click" };
      emit("DELEGATING");
      for (const invalid of [{ step_tool: {} }, { step_tool: "x".repeat(129) }, { step_outcome: "secret" }, { step_index: 3, step_count: 2 }]) {
        emit("RUNNING", "skill-call", { ...skill, ...invalid });
        expect(t.snap().activities[0].skillRunId).toBeUndefined();
      }
      emit("LISTENING", "skill-call", { ...skill, step_outcome: "waiting", outcome: "waiting" });
      emit("DONE");
      expect(t.snap().activities[0].phase).toBe("waiting");
      expect(t.snap().tracker.tool).toBeNull();
      await vi.advanceTimersByTimeAsync(35_000);
      expect(t.snap().activities[0].phase).toBe("waiting");
      emit("VERIFYING", "answer-call", { ...skill, tool_name: "answer_skill", step_outcome: "verifying" });
      expect(t.snap().activities).toHaveLength(1);
      expect(t.snap().activities[0].steps?.[0]).toMatchObject({ toolName: "browser_click", phase: "verifying" });
      t.room().emit(RoomEvent.ConnectionStateChanged, ConnectionState.Reconnecting);
      expect(t.snap().activities[0].steps?.[0].phase).toBe("unavailable");
      t.controller.dispose();
    });

    it("accepts correlated skill progress and refresh notifications", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      const who = t.room().remoteParticipants.get("agent-1");
      const emit = (state: string, overrides = {}) => t.room().emit(RoomEvent.DataReceived,
        executionPacket(state, "skill-call", { tool_name: "run_skill", ...overrides }),
        who, DataPacket_Kind.RELIABLE, "ira.status");
      emit("DELEGATING");
      emit("VERIFYING", {source: "skill", skill_run_id: "skill-1", skill_slug: "notes", skill_version: 2, step_index: 1, step_count: 2});
      expect(t.snap().activities[0]).toMatchObject({skillRunId: "skill-1", progress: "Step 1 of 2", phase: "verifying"});
      const events: unknown[] = [];
      t.controller.subscribeEvents((event) => events.push(event));
      t.room().emit(RoomEvent.DataReceived, new TextEncoder().encode('{"type":"skills.changed"}'), who, DataPacket_Kind.RELIABLE, "ira.events");
      expect(events).toEqual([{type: "skills.changed"}]);
    });

    const executionPacket = (state: string, callId = "call-1", overrides = {}) =>
      new TextEncoder().encode(JSON.stringify({
        state, detail: "read_page", run_id: "run-1", call_id: callId,
        tool_name: "read_page", source: "lifecycle", ...overrides,
      }));

    it("keeps separate actions and clears only finished history", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      const who = t.room().remoteParticipants.get("agent-1");
      const emit = (state: string, id: string) => t.room().emit(RoomEvent.DataReceived,
        executionPacket(state, id), who, DataPacket_Kind.RELIABLE, "ira.status");
      emit("DELEGATING", "call-1");
      emit("DELEGATING", "call-2");
      emit("DONE", "call-1");
      expect(t.snap().activities).toHaveLength(2);
      expect(t.snap().activities.find((item) => item.callId === "call-2")?.phase).toBe("running");
      expect(t.snap().tracker.tool?.name).toBe("read_page");
      t.controller.clearExecutionHistory();
      expect(t.snap().activities.map((item) => item.callId)).toEqual(["call-2"]);
    });

    it("rejects forged and malformed execution events", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      const who = t.room().remoteParticipants.get("agent-1");
      const emit = (payload: Uint8Array, sender: unknown = who, kind = DataPacket_Kind.RELIABLE) =>
        t.room().emit(RoomEvent.DataReceived, payload, sender, kind, "ira.status");
      emit(executionPacket("DELEGATING"), { identity: "fake", kind: ParticipantKind.AGENT });
      emit(executionPacket("DELEGATING"), who, DataPacket_Kind.LOSSY);
      for (const value of [null, [], { state: {} }, { state: "DONE", call_id: 7 }, { state: "DELEGATING", source: "lifecycle", run_id: "run", call_id: "x" }]) {
        emit(new TextEncoder().encode(JSON.stringify(value)));
      }
      expect(t.snap().tracker.tool).toBeNull();
      expect(t.snap().activities).toEqual([]);
      emit(executionPacket("DELEGATING"));
      expect(t.snap().activities).toHaveLength(1);
    });

    it("marks work unavailable on reconnect and expires a missing end", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      const emit = (state: string, id: string) => t.room().emit(RoomEvent.DataReceived,
        executionPacket(state, id), t.room().remoteParticipants.get("agent-1"),
        DataPacket_Kind.RELIABLE, "ira.status");
      emit("DELEGATING", "call-1");
      t.room().emit(RoomEvent.ConnectionStateChanged, ConnectionState.Reconnecting);
      expect(t.snap().activities[0].phase).toBe("unavailable");
      expect(t.snap().tracker.tool).toBeNull();
      t.room().emit(RoomEvent.ConnectionStateChanged, ConnectionState.Connected);
      emit("RUNNING", "call-1");
      expect(t.snap().activities[0].phase).toBe("running");
      await vi.advanceTimersByTimeAsync(30_001);
      expect(t.snap().activities[0].phase).toBe("unavailable");
      t.controller.dispose();
    });

    it("delivers reliable task notifications only from the room's agent", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();
      const events: unknown[] = [];
      const unsubscribe = t.controller.subscribeEvents((event) => events.push(event));
      const packet = new TextEncoder().encode(JSON.stringify({ type: "tasks.changed" }));
      const who = t.room().remoteParticipants.get("agent-1");
      const emit = (payload: Uint8Array, sender: unknown = who, kind = DataPacket_Kind.RELIABLE, topic = "ira.events") => t.room().emit(RoomEvent.DataReceived, payload, sender, kind, topic);
      emit(packet);
      expect(events).toEqual([{ type: "tasks.changed" }]);
      emit(packet, { identity: "intruder", kind: ParticipantKind.STANDARD, attributes: { "lk.agent.state": "listening" } });
      emit(packet, null);
      emit(packet, who, DataPacket_Kind.LOSSY);
      emit(packet, who, DataPacket_Kind.RELIABLE, "elsewhere");
      for (const value of ["bad json", "null", "[]", '{"type":"other"}', '{"type":"tasks.changed","task":{"title":"untrusted"}}']) emit(new TextEncoder().encode(value));
      expect(events).toHaveLength(1);
      unsubscribe();
      emit(packet);
      expect(events).toHaveLength(1);
    });

    it("keeps a tool in flight while she talks over it", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().agentJoins();

      t.room().status("DELEGATING", "remember");
      t.room().status("SPEAKING");

      expect(t.snap().tracker.tool?.name).toBe("remember");
    });

    it("ignores malformed status payloads", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);
      t.room().emit(
        RoomEvent.DataReceived,
        new TextEncoder().encode("not json"),
        undefined,
        undefined,
        "ira.status",
      );
      expect(t.snap().tracker.tool).toBeNull();
    });
  });

  describe("shell failures", () => {
    it("says so when the desktop shell can't report its processes", async () => {
      // An unhandled rejection here left the orb on CONNECTING forever with
      // nothing on screen to explain it.
      const t = setup();
      t.controller["options"].bridge.processes = async () => {
        throw new Error("No handler registered for 'ira:agent:status'");
      };
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      expect(t.snap().connection).toBe("disconnected");
      expect(t.snap().error).toMatch(/desktop shell/i);
      expect(t.tokenCalls()).toBe(0);
    });
  });

  describe("disposal", () => {
    it("disconnects and detaches every listener", async () => {
      const t = setup();
      await t.controller.start();
      await vi.advanceTimersByTimeAsync(0);

      t.controller.dispose();

      expect(t.room().disconnected).toBe(true);
      expect(t.room().listenerCount()).toBe(0);
    });

    it("ignores a token that arrives after disposal", async () => {
      // React StrictMode mounts, unmounts and remounts. The first mount's
      // in-flight token must not produce a second, orphaned connection.
      const t = setup({ agentState: "running" });
      await t.controller.start();
      t.pushProcess(agent("ready"));
      t.controller.dispose();
      await vi.advanceTimersByTimeAsync(10_000);

      expect(t.rooms).toHaveLength(0);
    });
  });
});
