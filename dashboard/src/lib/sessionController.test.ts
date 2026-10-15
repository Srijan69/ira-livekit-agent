import { ConnectionState, ParticipantKind, RoomEvent } from "livekit-client";
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
      undefined,
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
