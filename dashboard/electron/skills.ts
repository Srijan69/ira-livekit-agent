import type { SkillsApi, TaskResult } from "../shared/types";

type BridgeRequest = (path: string, init?: RequestInit) => Promise<Response>;
const badInput = { error: "Invalid skill request." };
const validSlug = (slug: unknown): slug is string =>
  typeof slug === "string" && /^[a-z0-9][a-z0-9_-]{0,127}$/.test(slug);

/** Fixed metadata operations. Execution stays on the agent's chat channel. */
export function createSkillProxy(callBridge: BridgeRequest): SkillsApi {
  async function request<T>(
    path: string,
    method: string,
    body?: string,
  ): Promise<TaskResult<T>> {
    try {
      const response = await callBridge(path, {
        method,
        body,
        signal: AbortSignal.timeout(10_000),
      });
      const data: unknown = await response.json();
      if (!response.ok) {
        const error =
          data && typeof data === "object" && "error" in data
            ? data.error
            : undefined;
        return {
          error:
            typeof error === "string"
              ? error
              : `Skills request failed (${response.status}).`,
        };
      }
      return data as T;
    } catch {
      return { error: "Skills bridge unavailable. Try again." };
    }
  }
  return {
    list(includeArchived = false) {
      if (typeof includeArchived !== "boolean")
        return Promise.resolve(badInput);
      return request(`/skills?include_archived=${includeArchived}`, "GET");
    },
    detail(slug, version) {
      if (!validSlug(slug)) return Promise.resolve(badInput);
      if (version !== undefined && (!Number.isInteger(version) || version < 1))
        return Promise.resolve(badInput);
      return request(
        `/skills/${encodeURIComponent(slug)}${version === undefined ? "" : `?version=${version}`}`,
        "GET",
      );
    },
    suggestions() {
      return request("/skills/suggestions", "GET");
    },
    history(limit = 50) {
      if (!Number.isInteger(limit) || limit < 1 || limit > 100)
        return Promise.resolve(badInput);
      return request(`/skills/history?limit=${limit}`, "GET");
    },
    pin(slug, pinned) {
      if (!validSlug(slug) || typeof pinned !== "boolean")
        return Promise.resolve(badInput);
      return request(
        `/skills/${encodeURIComponent(slug)}`,
        "PATCH",
        JSON.stringify({ pinned }),
      );
    },
    archive(slug) {
      if (!validSlug(slug)) return Promise.resolve(badInput);
      return request(`/skills/${encodeURIComponent(slug)}`, "DELETE");
    },
    restore(slug) {
      if (!validSlug(slug)) return Promise.resolve(badInput);
      return request(`/skills/${encodeURIComponent(slug)}/restore`, "POST");
    },
  };
}
