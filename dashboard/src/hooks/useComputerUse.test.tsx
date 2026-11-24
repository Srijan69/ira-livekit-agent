import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import type { IraEvent } from "@shared/types";
import { useComputerUse } from "./useComputerUse";

it("subscribes once, discards disconnected events, and clears session images on reconnect", () => {
  let listener: ((event: IraEvent) => void) | undefined;
  let subscriptions = 0;
  const subscribeEvents = (callback: (event: IraEvent) => void) => { subscriptions++; listener = callback; return () => { listener = undefined; }; };
  const { result, rerender, unmount } = renderHook(({ connection }) => useComputerUse({ subscribeEvents, connection }), { initialProps: { connection: "connected" } });
  act(() => listener?.({ type: "computer.observation", run_id: "run", call_id: "call", tool_name: "capture_screen", sequence: 1, phase: "unavailable" }));
  expect(result.current.phase).toBe("unavailable");
  rerender({ connection: "connecting" });
  expect(result.current.phase).toBeNull();
  act(() => listener?.({ type: "computer.observation", run_id: "old", call_id: "call", tool_name: "capture_screen", sequence: 2, phase: "unavailable" }));
  rerender({ connection: "connected" });
  expect(result.current.runId).toBeNull();
  expect(subscriptions).toBe(1);
  unmount();
  expect(listener).toBeUndefined();
});
