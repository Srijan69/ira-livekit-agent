import { describe, expect, it, vi } from "vitest";

import { createTaskProxy } from "./tasks";

const task = {
  id: "t1",
  title: "Prepare notes",
  due_at: null,
  done: false,
  created_at: "2026-10-15T10:00:00Z",
};
const reply = (body: unknown, status = 200) =>
  ({ ok: status < 400, status, json: async () => body }) as Response;

describe("named task bridge operations", () => {
  it("uses fixed paths and serializes only the permitted fields", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    const proxy = createTaskProxy(async (path, init) => {
      calls.push([path, init]);
      return reply(path.includes("?") ? { tasks: [task] } : { task });
    });
    expect(await proxy.list(8)).toEqual({ tasks: [task] });
    await proxy.create({ title: task.title, due_at: null });
    await proxy.update("t1", { done: true });
    await proxy.delete("t1");
    expect(calls).toEqual([
      ["/tasks?limit=8", { method: "GET", signal: expect.any(AbortSignal) }],
      [
        "/tasks",
        {
          method: "POST",
          body: JSON.stringify({ title: task.title, due_at: null }),
          signal: expect.any(AbortSignal),
        },
      ],
      [
        "/tasks/t1",
        {
          method: "PATCH",
          body: JSON.stringify({ done: true }),
          signal: expect.any(AbortSignal),
        },
      ],
      ["/tasks/t1", { method: "DELETE", signal: expect.any(AbortSignal) }],
    ]);
  });

  it("rejects malformed renderer input before making any request", async () => {
    const request = vi.fn();
    const proxy = createTaskProxy(request);
    for (const result of await Promise.all([
      proxy.list(0),
      proxy.list(101),
      proxy.list(NaN),
      proxy.update("../token", { done: true }),
      proxy.update("t1", { done: "true" } as never),
      proxy.create({ title: " " }),
      proxy.create({ title: "x", due_at: "tomorrow" }),
      proxy.delete(""),
    ]))
      expect(result).toHaveProperty("error");
    expect(request).not.toHaveBeenCalled();
  });

  it("reports unavailable and rejected operations as results", async () => {
    const proxy = createTaskProxy(async () =>
      reply({ error: "Task not found" }, 404),
    );
    expect(await proxy.update("missing", { done: true })).toEqual({
      error: "Task not found",
    });
    const offline = createTaskProxy(async () => {
      throw new Error("secret host detail");
    });
    expect(await offline.list()).toEqual({
      error: "Tasks bridge unavailable. Try again.",
    });
  });
});
