import type { PersonalMemoryApi, PersonalMemoryResult } from "../shared/types";

type BridgeRequest = (path: string, init?: RequestInit) => Promise<Response>;
type Proxy = Omit<PersonalMemoryApi, "onChanged">;
const invalid = { error: "Invalid profile or memory request." };
const integer = (value: unknown, min: number, max: number) => typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
const text = (value: unknown, max: number) => typeof value === "string" && value.length <= max && !/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value);

/** Fixed loopback operations; personal data stays out of URLs except search. */
export function createPersonalMemoryProxy(callBridge: BridgeRequest, changed: () => void = () => {}): Proxy {
  async function request<T>(path: string, method: string, input?: unknown): Promise<PersonalMemoryResult<T>> {
    try {
      const response = await callBridge(path, {
        method,
        ...(input === undefined ? {} : { body: JSON.stringify(input) }),
        signal: AbortSignal.timeout(10_000),
      });
      const body: unknown = await response.json();
      if (!body || typeof body !== "object" || Array.isArray(body)) return { error: "IRA returned an invalid memory response. Try again." };
      if (!response.ok || "error" in body) {
        const message = "error" in body && typeof body.error === "string" ? body.error : "Profile or memory request failed. Try again.";
        return { error: message, ...(response.status === 409 ? { conflict: true } : {}) };
      }
      if (method !== "GET") changed();
      return body as T;
    } catch {
      return { error: "IRA's profile and memory bridge is unavailable. Try again." };
    }
  }
  return {
    profile: () => request("/profile", "GET"),
    saveProfile(input) {
      if (!input || !text(input.name, 120) || !text(input.occupation, 200) || !text(input.preferences, 4000)
        || (input.age !== null && !integer(input.age, 0, 130))
        || (input.expected_revision !== undefined && !integer(input.expected_revision, 0, Number.MAX_SAFE_INTEGER))) return Promise.resolve(invalid);
      return request("/profile", "PATCH", { name: input.name, age: input.age, occupation: input.occupation, preferences: input.preferences, ...(input.expected_revision === undefined ? {} : { expected_revision: input.expected_revision }) });
    },
    resetProfile: () => request("/profile/reset", "POST", {}),
    settings: () => request("/memory/settings", "GET"),
    saveSettings(input) {
      if (!input || typeof input !== "object" || Array.isArray(input)
        || Object.keys(input).some((key) => !["enabled", "context_budget", "summary_trigger", "recent_turns", "summarizer_model", "expected_revision"].includes(key))
        || (input.enabled !== undefined && typeof input.enabled !== "boolean")
        || (input.context_budget !== undefined && !integer(input.context_budget, 4096, 128000))
        || (input.summary_trigger !== undefined && !integer(input.summary_trigger, 1024, 128000))
        || (input.recent_turns !== undefined && !integer(input.recent_turns, 4, 40))
        || (input.summarizer_model !== undefined && (!text(input.summarizer_model, 120) || !input.summarizer_model.trim()))
        || (input.expected_revision !== undefined && !integer(input.expected_revision, 0, Number.MAX_SAFE_INTEGER))) return Promise.resolve(invalid);
      return request("/memory/settings", "PATCH", input);
    },
    list(query = "", limit = 100) {
      if (!text(query, 2000) || !integer(limit, 1, 100)) return Promise.resolve(invalid);
      return request(`/memory?query=${encodeURIComponent(query)}&limit=${limit}`, "GET");
    },
    status: () => request("/memory/status", "GET"),
    deleteMemory(id) {
      if (typeof id !== "string" || !/^[\w:-]{1,160}$/.test(id)) return Promise.resolve(invalid);
      return request(`/memory/${encodeURIComponent(id)}`, "DELETE");
    },
    clearMemory: () => request("/memory/clear", "POST", {}),
  };
}
