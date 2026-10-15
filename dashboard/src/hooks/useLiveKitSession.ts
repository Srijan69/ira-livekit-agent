import { LocalAudioTrack, RemoteAudioTrack, Room } from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

import { AudioMeter } from "@/lib/audioMeter";
import {
  ERROR_FLASH_MS,
  TOOL_STALE_MS,
  activeCuesFrom,
  orbStateFrom,
  type OrbInput,
  type OrbState,
} from "@/lib/orbState";
import type { Session } from "@/lib/session";
import {
  INITIAL_SNAPSHOT,
  SessionController,
  type RoomLike,
  type SessionSnapshot,
} from "@/lib/sessionController";
import { orbStatusFrom } from "@/lib/statusTracker";

const NO_BRIDGE =
  "No desktop bridge -- this is the browser preview. Run the Electron app to talk to IRA.";

/**
 * The live session.
 *
 * A thin adapter: the connection rules live in `SessionController`, where
 * they are tested against a fake room, and the audio in `AudioMeter`. This
 * hook only turns their output into the `Session` shape the UI consumes.
 *
 * Built on `livekit-client` directly rather than `@livekit/components-react`.
 * The orb's state machine was written against plain facts -- connection,
 * presence, lifecycle, loudness -- so the components package's provider and
 * prebuilt UI would add layers without removing any code.
 */
export function useLiveKitSession(): Session {
  const [snapshot, setSnapshot] = useState<SessionSnapshot>(INITIAL_SNAPSHOT);
  const controllerRef = useRef<SessionController | null>(null);
  const meterRef = useRef<AudioMeter | null>(null);

  useEffect(() => {
    const bridge = window.ira;
    if (!bridge) {
      setSnapshot({ ...INITIAL_SNAPSHOT, error: NO_BRIDGE });
      return;
    }

    const meter = new AudioMeter();
    const controller = new SessionController({
      bridge: {
        token: () => bridge.session.token(),
        health: () => bridge.session.health(),
        processes: () => bridge.agent.status(),
        onProcess: (listener) => bridge.agent.onStatus(listener),
      },
      // The one cast at the boundary: Room's typed event overloads don't
      // unify with RoomLike's deliberately loose `on`. RoomLike lists exactly
      // what the controller uses, and the controller's tests pin that.
      createRoom: () =>
        new Room({ adaptiveStream: true, dynacast: true }) as unknown as RoomLike,
      onAgentAudio: (track) => {
        if (track instanceof RemoteAudioTrack) meter.attachAgent(track);
      },
      onMicTrack: (track) => {
        if (track instanceof LocalAudioTrack) meter.attachMic(track.mediaStreamTrack);
      },
    });

    meterRef.current = meter;
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe(setSnapshot);
    void controller.start();

    // StrictMode mounts, unmounts and remounts in development. Each mount owns
    // its own controller, so the first one's in-flight connect is disposed
    // rather than raced.
    return () => {
      unsubscribe();
      controller.dispose();
      meter.close();
      controllerRef.current = null;
      meterRef.current = null;
    };
  }, []);

  // The error flash and the stale-tool safety net are both functions of time.
  // Nothing in the session changes when they lapse, so schedule the re-render
  // that lets the orb settle back -- rather than leaving it amber until some
  // unrelated update happens along.
  const [, setTick] = useState(0);
  useEffect(() => {
    const { errorAt, tool } = snapshot.tracker;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const wakeAt = (deadline: number) => {
      const wait = deadline - Date.now();
      if (wait > 0) timers.push(setTimeout(() => setTick((n) => n + 1), wait + 16));
    };
    if (errorAt !== null) wakeAt(errorAt + ERROR_FLASH_MS);
    if (tool) wakeAt(tool.lastUpdate + TOOL_STALE_MS);
    return () => timers.forEach(clearTimeout);
  }, [snapshot.tracker]);

  const now = Date.now();
  const input: OrbInput = {
    connection: snapshot.connection,
    agentPresent: snapshot.agentPresent,
    agentState: snapshot.agentState,
    userSpeaking: snapshot.userSpeaking,
    lastStatus: orbStatusFrom(snapshot.tracker, now),
    now,
  };
  const orbState = orbStateFrom(input);

  // Read by the waveform's frame loop, so it lives in a ref: a per-frame
  // callback must not re-render anything.
  const orbRef = useRef<OrbState>(orbState);
  orbRef.current = orbState;
  const micRef = useRef(snapshot.micEnabled);
  micRef.current = snapshot.micEnabled;

  const getLevel = useCallback(() => {
    const meter = meterRef.current;
    if (!meter) return 0;
    // Her voice while she speaks, the boss's while they do.
    if (orbRef.current === "speaking") return meter.level("agent");
    if (orbRef.current === "recognizing") return meter.level("mic");
    return 0;
  }, []);

  const toggleMic = useCallback(() => {
    controllerRef.current?.setMicrophone(!micRef.current);
  }, []);

  const send = useCallback((text: string) => {
    controllerRef.current?.send(text);
  }, []);

  return {
    connection: snapshot.connection,
    orbState,
    cues: activeCuesFrom(input),
    micEnabled: snapshot.micEnabled,
    toggleMic,
    getLevel,
    send,
    error: snapshot.error,
  };
}
