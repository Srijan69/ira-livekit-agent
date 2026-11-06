import { describe, expect, it } from "vitest";
import { createSkillProxy } from "./skills";

describe("skill management boundary", () => {
  it("rejects unsafe slugs and malformed mutations before any bridge request", async () => {
    const calls: string[] = [];
    const api = createSkillProxy(async (path) => {
      calls.push(path);
      return Response.json({});
    });
    expect(await api.detail("../secret")).toHaveProperty("error");
    expect(await api.pin("notes", "yes" as unknown as boolean)).toHaveProperty(
      "error",
    );
    expect(await api.history(1000)).toHaveProperty("error");
    expect(calls).toEqual([]);
  });

  it("exposes fixed metadata and history routes without an execution operation", async () => {
    const calls: Array<[string, string | undefined, unknown]> = [];
    const api = createSkillProxy(async (path, init) => {
      calls.push([path, init?.method, init?.body]);
      return Response.json({ skills: [] });
    });
    await api.list(true);
    await api.pin("notes", true);
    await api.archive("notes");
    await api.restore("notes");
    expect(calls).toEqual([
      ["/skills?include_archived=true", "GET", undefined],
      ["/skills/notes", "PATCH", '{"pinned":true}'],
      ["/skills/notes", "DELETE", undefined],
      ["/skills/notes/restore", "POST", undefined],
    ]);
    expect(api).not.toHaveProperty("run");
    expect(api).not.toHaveProperty("stop");
  });

  it("turns bridge failures into reviewable errors", async () => {
    const api = createSkillProxy(async () =>
      Response.json({ error: "Skill not found" }, { status: 404 }),
    );
    expect(await api.detail("notes")).toEqual({ error: "Skill not found" });
  });
});
