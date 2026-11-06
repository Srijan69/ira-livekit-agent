import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type {
  PersonalMemoryApi,
  UserProfile,
  UserProfileInput,
} from "@shared/types";

import { usePersonalMemory } from "./usePersonalMemory";

const profile: UserProfile = {
  name: "Ada Lovelace",
  age: null,
  occupation: "Engineer",
  preferences: "Brief answers",
  revision: 2,
  updated_at: "2026-11-06T10:00:00Z",
};
const settings = {
  enabled: true,
  context_budget: 16000,
  summary_trigger: 12000,
  recent_turns: 12,
  summarizer_model: "gemini-3.8-flash",
  revision: 0,
};
function makeApi(
  overrides: Partial<PersonalMemoryApi> = {},
): PersonalMemoryApi {
  return {
    profile: async () => profile,
    saveProfile: async (input) => ({ ...profile, ...input, revision: 3 }),
    resetProfile: async () => ({
      ...profile,
      name: "",
      age: null,
      occupation: "",
      preferences: "",
      revision: 3,
    }),
    settings: async () => settings,
    saveSettings: async (input) => ({ ...settings, ...input, revision: 1 }),
    status: async () => ({
      settings,
      memory_revision: 0,
      checkpoint: null,
      status: { state: "collecting" },
    }),
    list: async () => ({ memories: [] }),
    deleteMemory: async () => ({ deleted: true }),
    clearMemory: async () => ({ cleared: true }),
    onChanged: () => () => {},
    ...overrides,
  };
}
afterEach(cleanup);

describe("usePersonalMemory", () => {
  it("loads the authoritative profile and memory status through the optional bridge", async () => {
    const api = makeApi();
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() =>
      expect(result.current.profile?.name).toBe("Ada Lovelace"),
    );
    expect(result.current.settings?.context_budget).toBe(16000);
    expect(result.current.memoryStatus?.status?.state).toBe("collecting");
    expect(result.current.available).toBe(true);
  });

  it("explains unavailable desktop memory without inventing a profile", async () => {
    const { result } = renderHook(() => usePersonalMemory(null));
    expect(result.current.available).toBe(false);
    expect(result.current.profile).toBeNull();
    expect(result.current.profileError).toMatch(/desktop app/i);
  });

  it("prevents a stale refresh from replacing a committed profile", async () => {
    let changed!: () => void;
    let finishRead!: (value: UserProfile) => void;
    let reads = 0;
    const api = makeApi({
      profile: () =>
        ++reads === 1
          ? Promise.resolve(profile)
          : new Promise((resolve) => {
              finishRead = resolve;
            }),
      onChanged: (listener) => {
        changed = listener;
        return () => {};
      },
    });
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() => expect(result.current.profile?.revision).toBe(2));
    await act(async () => changed());
    await act(async () => {
      await result.current.saveProfile(
        { name: "Grace Hopper", age: null, occupation: "", preferences: "" },
        2,
      );
      finishRead(profile);
    });
    expect(result.current.profile?.name).toBe("Grace Hopper");
    expect(result.current.profile?.revision).toBe(3);
  });

  it("reports a revision conflict and refreshes canonical data", async () => {
    let canonical = profile;
    let received:
      (UserProfileInput & { expected_revision?: number }) | undefined;
    const api = makeApi({
      profile: async () => canonical,
      saveProfile: async (input) => {
        received = input;
        canonical = { ...profile, name: "Changed by voice", revision: 3 };
        return {
          error: "Profile changed. Reload and try again.",
          conflict: true,
        };
      },
    });
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() => expect(result.current.profile?.revision).toBe(2));
    let response: unknown;
    await act(async () => {
      response = await result.current.saveProfile(
        { name: "My edit", age: null, occupation: "", preferences: "" },
        2,
      );
    });
    expect(received?.expected_revision).toBe(2);
    expect(response).toEqual({
      error: "Profile changed. Reload and try again.",
      conflict: true,
    });
    await waitFor(() =>
      expect(result.current.profile?.name).toBe("Changed by voice"),
    );
  });

  it("ignores an older search response and refreshes after deletion", async () => {
    let finishOld!: (value: {
      memories: {
        id: string;
        kind: string;
        content: string;
        source: string;
        created_at: string;
      }[];
    }) => void;
    let deleted = false;
    const api = makeApi({
      list: async (query) => {
        if (query === "old")
          return new Promise((resolve) => {
            finishOld = resolve;
          });
        return {
          memories: deleted
            ? []
            : [
                {
                  id: "fact-1",
                  kind: "fact",
                  content: query === "new" ? "New result" : "Saved fact",
                  source: "explicit",
                  created_at: "2026-11-06T10:00:00Z",
                },
              ],
        };
      },
      deleteMemory: async (id) => {
        deleted = id === "fact-1";
        return { deleted };
      },
    });
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() => expect(result.current.memories).toHaveLength(1));
    act(() => result.current.setQuery("old"));
    act(() => result.current.setQuery("new"));
    await waitFor(() =>
      expect(result.current.memories[0]?.content).toBe("New result"),
    );
    await act(async () =>
      finishOld({
        memories: [
          {
            id: "stale",
            kind: "fact",
            content: "Old result",
            source: "explicit",
            created_at: "",
          },
        ],
      }),
    );
    expect(result.current.memories[0]?.content).toBe("New result");
    await act(async () => {
      await result.current.deleteMemory("fact-1");
    });
    await waitFor(() => expect(result.current.memories).toEqual([]));
    expect(result.current.profile?.name).toBe("Ada Lovelace");
  });

  it("keeps memory when the backend does not confirm deletion or clearing", async () => {
    const api = makeApi({
      list: async () => ({
        memories: [
          {
            id: "saved",
            kind: "fact",
            content: "Keep this detail",
            source: "explicit",
            created_at: "2026-11-06T10:00:00Z",
          },
        ],
      }),
      deleteMemory: async () => ({ deleted: false }),
      clearMemory: async () => ({ cleared: false }),
    });
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() => expect(result.current.memories).toHaveLength(1));
    let deletion: unknown;
    await act(async () => {
      deletion = await result.current.deleteMemory("saved");
    });
    expect(deletion).toEqual({ error: "Could not delete memory. Try again." });
    expect(result.current.memories[0]?.content).toBe("Keep this detail");
    let clearing: unknown;
    await act(async () => {
      clearing = await result.current.clearMemory();
    });
    expect(clearing).toEqual({
      error: "Could not clear conversation memory. Try again.",
    });
    expect(result.current.memories[0]?.content).toBe("Keep this detail");
  });

  it("allows one profile save at a time and releases the lock after failure", async () => {
    let complete!: (value: { error: string }) => void;
    let saves = 0;
    const api = makeApi({
      saveProfile: async (input) => {
        saves += 1;
        if (saves === 1)
          return new Promise((resolve) => {
            complete = resolve;
          });
        return { ...profile, ...input, revision: 3 };
      },
    });
    const { result } = renderHook(() => usePersonalMemory(api));
    await waitFor(() => expect(result.current.profile?.revision).toBe(2));
    const input = {
      name: "Saved once",
      age: null,
      occupation: "",
      preferences: "",
    };
    let first: Promise<unknown>;
    act(() => {
      first = result.current.saveProfile(input, 2);
    });
    await act(async () => {
      await result.current.saveProfile(input, 2);
    });
    expect(saves).toBe(1);
    expect(result.current.savingProfile).toBe(true);
    await act(async () => {
      complete({ error: "Temporary failure" });
      await first;
    });
    expect(result.current.savingProfile).toBe(false);
    await act(async () => {
      await result.current.saveProfile(input, 2);
    });
    expect(result.current.profile?.name).toBe("Saved once");
  });
});
