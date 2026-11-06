import { useCallback, useEffect, useRef, useState } from "react";

import type { IraEvent, ProcessStatus, Task, TasksApi } from "@shared/types";

type Options = {
  api?: TasksApi | null;
  subscribeEvents?: (listener: (event: IraEvent) => void) => () => void;
  onProcess?: (listener: (status: ProcessStatus) => void) => () => void;
  connection?: string;
};

export function useTasks(options: Options = {}) {
  const api = options.api === undefined ? window.ira?.tasks : options.api;
  const onProcess = options.onProcess ?? window.ira?.agent.onStatus;
  const { subscribeEvents, connection } = options;
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const pending = useRef(new Map<string, { before: Task; done: boolean }>());
  const rows = useRef(tasks);
  rows.current = tasks;
  const active = useRef(false);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    const id = ++requestId.current;
    if (!api) {
      setLoading(false);
      setError("Open the desktop app to load your tasks.");
      return;
    }
    try {
      const response = await api.list(expanded ? 100 : 8);
      if (!active.current || id !== requestId.current) return;
      if ("error" in response) setError(response.error);
      else {
        setTasks(
          response.tasks.map((task) => {
            const change = pending.current.get(task.id);
            return change ? { ...task, done: change.done } : task;
          }),
        );
        setError(null);
      }
    } catch {
      if (active.current && id === requestId.current)
        setError("Tasks bridge unavailable. Try again.");
    } finally {
      if (active.current && id === requestId.current) setLoading(false);
    }
  }, [api, expanded]);
  // A save can finish after View All changes the list limit.
  const latestRefresh = useRef(refresh);
  latestRefresh.current = refresh;

  useEffect(() => {
    active.current = true;
    void refresh();
    const unsubscribeEvent = subscribeEvents?.((event) => {
      if (event.type === "tasks.changed") void refresh();
    });
    const unsubscribeProcess = onProcess?.((status) => {
      if (status.name === "bridge" && status.state === "ready") void refresh();
    });
    return () => {
      active.current = false;
      requestId.current += 1;
      unsubscribeEvent?.();
      unsubscribeProcess?.();
    };
  }, [refresh, subscribeEvents, onProcess]);

  const previousConnection = useRef(connection);
  useEffect(() => {
    if (
      connection === "connected" &&
      previousConnection.current !== "connected"
    )
      void refresh();
    previousConnection.current = connection;
  }, [connection, refresh]);

  const toggle = useCallback(
    async (id: string) => {
      if (!api || pending.current.has(id)) return;
      const before = rows.current.find((task) => task.id === id);
      if (!before) return;
      const done = !before.done;
      pending.current.set(id, { before, done });
      setPendingIds(new Set(pending.current.keys()));
      setTasks((current) =>
        current.map((task) => (task.id === id ? { ...task, done } : task)),
      );
      setError(null);
      try {
        const result = await api.update(id, { done });
        if ("error" in result) throw new Error(result.error);
        if (!active.current) return;
        pending.current.delete(id);
        setTasks((current) =>
          current.map((task) => (task.id === id ? result.task : task)),
        );
        void latestRefresh.current();
      } catch (cause) {
        if (!active.current) return;
        setTasks((current) =>
          current.map((task) => (task.id === id ? before : task)),
        );
        setError(
          cause instanceof Error
            ? cause.message
            : "Task could not be saved. Try again.",
        );
      } finally {
        pending.current.delete(id);
        if (active.current) setPendingIds(new Set(pending.current.keys()));
      }
    },
    [api],
  );

  return {
    tasks,
    loading,
    error,
    pendingIds,
    refresh,
    toggle,
    expanded,
    setExpanded,
  };
}
