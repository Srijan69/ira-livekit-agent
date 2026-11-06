import type { TaskResult, TasksApi } from "../shared/types";

type BridgeRequest = (path: string, init?: RequestInit) => Promise<Response>;
const badInput = { error: "Invalid task request." };
const validId = (id: unknown): id is string =>
  typeof id === "string" && /^[\w-]{1,128}$/.test(id);

/** A fixed set of operations, never a renderer-selected URL or HTTP method. */
export function createTaskProxy(callBridge: BridgeRequest): TasksApi {
  async function request<T>(
    path: string,
    init: RequestInit,
  ): Promise<TaskResult<T>> {
    try {
      const response = await callBridge(path, {
        ...init,
        signal: AbortSignal.timeout(10_000),
      });
      const body: unknown = await response.json();
      if (!response.ok) {
        const error =
          body && typeof body === "object" && "error" in body
            ? body.error
            : null;
        return {
          error:
            typeof error === "string"
              ? error
              : `Tasks request failed (${response.status}).`,
        };
      }
      return body as T;
    } catch {
      return { error: "Tasks bridge unavailable. Try again." };
    }
  }
  return {
    list(limit = 8) {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100)
        return Promise.resolve(badInput);
      return request(`/tasks?limit=${limit}`, { method: "GET" });
    },
    create(input) {
      if (
        !input ||
        typeof input.title !== "string" ||
        !input.title.trim() ||
        input.title.length > 500
      )
        return Promise.resolve(badInput);
      const due = input.due_at;
      if (
        due != null &&
        (typeof due !== "string" ||
          !/(?:Z|[+-]\d{2}:\d{2})$/i.test(due) ||
          !Number.isFinite(Date.parse(due)))
      )
        return Promise.resolve(badInput);
      return request("/tasks", {
        method: "POST",
        body: JSON.stringify({ title: input.title, due_at: due }),
      });
    },
    update(id, patch) {
      if (!validId(id) || !patch || typeof patch.done !== "boolean")
        return Promise.resolve(badInput);
      return request(`/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ done: patch.done }),
      });
    },
    delete(id) {
      if (!validId(id)) return Promise.resolve(badInput);
      return request(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
    },
  };
}
