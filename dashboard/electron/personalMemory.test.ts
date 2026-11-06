// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createPersonalMemoryProxy } from "./personalMemory";

describe("personal memory bridge proxy", () => {
  it("uses fixed profile routes with revisions and notifies only after success", async () => {
    const calls: Array<{path: string; init?: RequestInit}> = [];
    let notifications = 0;
    const proxy = createPersonalMemoryProxy(async (path, init) => {
      calls.push({path, init});
      return new Response(JSON.stringify({ name: "Alex", age: null, occupation: "Engineer", preferences: "Concise replies", revision: 1, updated_at: "2026-11-06T00:00:00Z" }));
    }, () => { notifications++; });
    const input = { name: "Alex", age: null, occupation: "Engineer", preferences: "Concise replies", expected_revision: 0 };
    expect(await proxy.saveProfile(input)).toHaveProperty("revision", 1);
    expect(calls[0].path).toBe("/profile");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(input);
    expect(calls[0].init?.signal).toBeInstanceOf(AbortSignal);
    expect(notifications).toBe(1);
    await proxy.profile();
    expect(notifications).toBe(1);
  });

  it("reports concurrent-edit conflicts and preserves the request revision", async () => {
    let notifications = 0;
    const proxy = createPersonalMemoryProxy(async () => new Response('{"error":"Profile changed. Refresh and try again."}', {status: 409}), () => { notifications++; });
    expect(await proxy.saveProfile({ name: "New", age: 32, occupation: "", preferences: "", expected_revision: 3 })).toMatchObject({error: expect.any(String), conflict: true});
    expect(notifications).toBe(0);
  });

  it("rejects unsafe profile/settings/deletion requests without touching the bridge", async () => {
    let calls = 0;
    const proxy = createPersonalMemoryProxy(async () => { calls++; return new Response('{}'); });
    expect(await proxy.saveProfile({name: "", age: -1, occupation: "", preferences: ""})).toHaveProperty("error");
    expect(await proxy.saveSettings({ enabled: "yes" as unknown as boolean })).toHaveProperty("error");
    expect(await proxy.deleteMemory("../../profile")).toHaveProperty("error");
    expect(await proxy.list("query", 1000)).toHaveProperty("error");
    expect(calls).toBe(0);
  });

  it("encodes search text while keeping memory clear separate from profile reset", async () => {
    const paths: string[] = [];
    const proxy = createPersonalMemoryProxy(async (path) => { paths.push(path); return new Response('{}'); });
    await proxy.list("name & age", 20);
    await proxy.clearMemory();
    await proxy.resetProfile();
    expect(paths).toEqual(["/memory?query=name%20%26%20age&limit=20", "/memory/clear", "/profile/reset"]);
  });

  it("reports unavailable/corrupt bridge responses without leaking thrown text", async () => {
    const proxy = createPersonalMemoryProxy(async () => { throw new Error("private user profile details"); });
    const result = await proxy.profile();
    expect(result).toHaveProperty("error");
    expect(JSON.stringify(result)).not.toContain("private user profile details");
    const corrupt = createPersonalMemoryProxy(async () => new Response("not JSON"));
    expect(await corrupt.status()).toHaveProperty("error");
  });
});
