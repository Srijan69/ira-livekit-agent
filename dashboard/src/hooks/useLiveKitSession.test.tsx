import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { ProcessSnapshot } from "@shared/types";
import { useLiveKitSession } from "./useLiveKitSession";

const rooms = vi.hoisted(() => [] as Array<{ disconnect: ReturnType<typeof vi.fn>; microphone: ReturnType<typeof vi.fn> }>);
vi.mock("livekit-client", async (importOriginal) => {
  const original = await importOriginal<typeof import("livekit-client")>();
  return {
    ...original,
    Room: class {
      state = original.ConnectionState.Connected;
      remoteParticipants = new Map();
      disconnect = vi.fn(async () => {});
      microphone = vi.fn(async () => {});
      localParticipant = { isSpeaking: false, setMicrophoneEnabled: this.microphone };
      constructor() { rooms.push(this); }
      async connect() {}
      on() {} off() {}
    },
  };
});

afterEach(() => { delete window.ira; rooms.length = 0; });

it("connects after the first save and disposes the old voice session when keys change again", async () => {
  let changed: (() => void) | undefined;
  let processes: ProcessSnapshot = {
    supervised: true, configurationRequired: true,
    agent: { name: "agent", state: "stopped", restarts: 0 }, bridge: null,
  };
  window.ira = {
    connection: { onChanged: (listener: () => void) => { changed = listener; return () => { changed = undefined; }; } },
    session: { token: async () => ({ url: "wss://test.example", token: "fake-token", room: "test" }) },
    agent: { status: async () => processes, onStatus: () => () => {} },
  } as unknown as NonNullable<typeof window.ira>;

  const hook = renderHook(() => useLiveKitSession());
  await waitFor(() => expect(hook.result.current.error).toContain("Connection and Keys"));
  expect(rooms).toHaveLength(0);
  processes = { ...processes, configurationRequired: false, agent: { name: "agent", state: "ready", restarts: 0 } };
  act(() => changed!());
  await waitFor(() => expect(hook.result.current.connection).toBe("connected"));
  expect(rooms).toHaveLength(1);
  expect(rooms[0].microphone).toHaveBeenCalledWith(true);

  act(() => changed!());
  await waitFor(() => expect(rooms).toHaveLength(2));
  expect(rooms[0].disconnect).toHaveBeenCalledOnce();
  hook.unmount();
  expect(rooms[1].disconnect).toHaveBeenCalledOnce();
  expect(changed).toBeUndefined();
});
