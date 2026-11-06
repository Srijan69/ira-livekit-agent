import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { IraEvent, ProcessStatus, Task, TasksApi } from "@shared/types";
import { useTasks } from "./useTasks";

const task: Task = {
  id: "t1",
  title: "Prepare notes",
  due_at: null,
  done: false,
  created_at: "2026-10-15T10:00:00Z",
};
function fixture() {
  let rows = [task];
  let event: ((event: IraEvent) => void) | undefined;
  let process: ((status: ProcessStatus) => void) | undefined;
  const api: TasksApi = {
    list: vi.fn(async () => ({ tasks: rows })),
    create: vi.fn(),
    delete: vi.fn(),
    update: vi.fn(async (id, patch) => ({ task: { ...task, id, ...patch } })),
  };
  const subscribeEvents = (listener: (event: IraEvent) => void) => {
    event = listener;
    return () => {
      event = undefined;
    };
  };
  const onProcess = (listener: (status: ProcessStatus) => void) => {
    process = listener;
    return () => {
      process = undefined;
    };
  };
  return {
    api,
    subscribeEvents,
    onProcess,
    setRows: (next: Task[]) => {
      rows = next;
    },
    event: () => event?.({ type: "tasks.changed" }),
    ready: () => process?.({ name: "bridge", state: "ready", restarts: 0 }),
  };
}

describe("useTasks", () => {
  it("loads without an agent, and refreshes on task changes and bridge recovery", async () => {
    const f = fixture();
    const { result } = renderHook(() =>
      useTasks({ ...f, connection: "disconnected" }),
    );
    await act(async () => {});
    expect(result.current.tasks).toEqual([task]);
    f.setRows([{ ...task, title: "Added by voice" }]);
    await act(async () => {
      f.event();
    });
    expect(result.current.tasks[0].title).toBe("Added by voice");
    f.setRows([]);
    await act(async () => {
      f.ready();
    });
    expect(result.current.tasks).toEqual([]);
  });

  it("toggles immediately, blocks duplicate writes, and rolls back a rejected save", async () => {
    const f = fixture();
    let resolve!: (value: { error: string }) => void;
    f.api.update = vi.fn(
      () =>
        new Promise<{ error: string }>((done) => {
          resolve = done;
        }),
    );
    const { result } = renderHook(() => useTasks(f));
    await act(async () => {});
    act(() => {
      void result.current.toggle("t1");
    });
    expect(result.current.tasks[0].done).toBe(true);
    act(() => {
      void result.current.toggle("t1");
    });
    expect(f.api.update).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve({ error: "Database busy" });
    });
    expect(result.current.tasks[0].done).toBe(false);
    expect(result.current.error).toContain("Database busy");
  });

  it("does not let an older response overwrite a newer notification refresh", async () => {
    const f = fixture();
    let finishOld!: (value: { tasks: Task[] }) => void;
    f.api.list = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((done) => {
            finishOld = done;
          }),
      )
      .mockResolvedValue({ tasks: [{ ...task, title: "New task" }] });
    const { result } = renderHook(() => useTasks(f));
    await act(async () => {
      f.event();
    });
    expect(result.current.tasks[0].title).toBe("New task");
    await act(async () => {
      finishOld({ tasks: [task] });
    });
    expect(result.current.tasks[0].title).toBe("New task");
  });

  it("fetches an expanded list and refreshes after a connection returns", async () => {
    const f = fixture();
    const { result, rerender } = renderHook(
      ({ connection }) => useTasks({ ...f, connection }),
      { initialProps: { connection: "disconnected" } },
    );
    await act(async () => {});
    await act(async () => {
      result.current.setExpanded(true);
    });
    expect(f.api.list).toHaveBeenLastCalledWith(100);
    const previous = vi.mocked(f.api.list).mock.calls.length;
    rerender({ connection: "connected" });
    await act(async () => {});
    expect(vi.mocked(f.api.list).mock.calls.length).toBeGreaterThan(previous);
  });

  it("shows an honest unavailable state without the desktop bridge", async () => {
    const { result } = renderHook(() => useTasks({ api: null }));
    await act(async () => {});
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toMatch(/desktop app/i);
  });

  it("keeps the expanded list when an earlier checkbox save finishes", async () => {
    const f = fixture();
    const extra = Array.from({ length: 10 }, (_, i) => ({
      ...task,
      id: String(i + 2),
    }));
    f.api.list = vi.fn(async (limit = 8) => ({
      tasks: [task, ...extra].slice(0, limit),
    }));
    let finishSave!: (value: { task: Task }) => void;
    f.api.update = vi.fn(
      () =>
        new Promise<{ task: Task }>((resolve) => {
          finishSave = resolve;
        }),
    );
    const { result } = renderHook(() => useTasks(f));
    await act(async () => {});
    act(() => {
      void result.current.toggle("t1");
    });
    await act(async () => {
      result.current.setExpanded(true);
    });
    expect(result.current.tasks).toHaveLength(11);
    await act(async () => {
      finishSave({ task: { ...task, done: true } });
    });
    expect(result.current.expanded).toBe(true);
    expect(result.current.tasks).toHaveLength(11);
  });
});
