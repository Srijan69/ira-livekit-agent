import { useCallback, useEffect, useRef, useState } from "react";
import type {
  MemoryRecord,
  MemorySettings,
  PersonalMemoryApi,
  PersonalMemoryResult,
  PersonalMemoryStatus,
  UserProfile,
  UserProfileInput,
} from "@shared/types";

const unavailable =
  "Open IRA in the desktop app to manage your profile and memory.";

/** One authoritative renderer snapshot; drafts stay in the Settings forms. */
export function usePersonalMemory(
  api: PersonalMemoryApi | null | undefined = window.ira?.personalMemory,
) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [memoryStatus, setMemoryStatus] = useState<PersonalMemoryStatus | null>(
    null,
  );
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [query, setQuery] = useState("");
  const [profileError, setProfileError] = useState<string | null>(
    api ? null : unavailable,
  );
  const [memoryError, setMemoryError] = useState<string | null>(
    api ? null : unavailable,
  );
  const [listError, setListError] = useState<string | null>(null);
  const [profileLoading, setProfileLoading] = useState(Boolean(api));
  const [memoryLoading, setMemoryLoading] = useState(Boolean(api));
  const [listLoading, setListLoading] = useState(Boolean(api));
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [changingMemory, setChangingMemory] = useState(false);
  const active = useRef(false);
  const generation = useRef(0);
  const sequence = useRef({ profile: 0, status: 0, list: 0 });
  const pending = useRef(new Set<string>());
  const currentQuery = useRef(query);
  currentQuery.current = query;

  const refreshProfile = useCallback(async () => {
    if (!api) return;
    const id = ++sequence.current.profile;
    const scope = generation.current;
    try {
      const result = await api.profile();
      if (
        !active.current ||
        scope !== generation.current ||
        id !== sequence.current.profile
      )
        return;
      if ("error" in result) setProfileError(result.error);
      else {
        setProfile((previous) =>
          !previous || result.revision >= previous.revision ? result : previous,
        );
        setProfileError(null);
      }
    } catch {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.profile
      )
        setProfileError("Could not load your profile. Try again.");
    } finally {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.profile
      )
        setProfileLoading(false);
    }
  }, [api]);

  const refreshStatus = useCallback(async () => {
    if (!api) return;
    const id = ++sequence.current.status;
    const scope = generation.current;
    try {
      const result = await api.status();
      if (
        !active.current ||
        scope !== generation.current ||
        id !== sequence.current.status
      )
        return;
      if ("error" in result) setMemoryError(result.error);
      else {
        setSettings((previous) =>
          !previous || result.settings.revision >= previous.revision
            ? result.settings
            : previous,
        );
        setMemoryStatus((previous) =>
          !previous || result.memory_revision >= previous.memory_revision
            ? result
            : previous,
        );
        setMemoryError(null);
      }
    } catch {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.status
      )
        setMemoryError("Could not load memory status. Try again.");
    } finally {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.status
      )
        setMemoryLoading(false);
    }
  }, [api]);

  const refreshList = useCallback(async () => {
    if (!api) return;
    const id = ++sequence.current.list;
    const scope = generation.current;
    setListLoading(true);
    try {
      const result = await api.list(currentQuery.current, 100);
      if (
        !active.current ||
        scope !== generation.current ||
        id !== sequence.current.list
      )
        return;
      if ("error" in result) setListError(result.error);
      else {
        setMemories(result.memories);
        setListError(null);
      }
    } catch {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.list
      )
        setListError("Could not load your memories. Try again.");
    } finally {
      if (
        active.current &&
        scope === generation.current &&
        id === sequence.current.list
      )
        setListLoading(false);
    }
  }, [api]);

  const refresh = useCallback(async () => {
    await Promise.allSettled([
      refreshProfile(),
      refreshStatus(),
      refreshList(),
    ]);
  }, [refreshProfile, refreshStatus, refreshList]);

  useEffect(() => {
    active.current = true;
    generation.current += 1;
    setProfile(null);
    setSettings(null);
    setMemoryStatus(null);
    setMemories([]);
    setProfileLoading(Boolean(api));
    setMemoryLoading(Boolean(api));
    setListLoading(Boolean(api));
    setProfileError(api ? null : unavailable);
    setMemoryError(api ? null : unavailable);
    setListError(null);
    void refreshProfile();
    void refreshStatus();
    const unsubscribe = api?.onChanged(() => {
      void refresh();
    });
    // Also recover when the bridge starts later, and surface live controller
    // status between durable-change notifications.
    const timer = api
      ? window.setInterval(() => {
          void refresh();
        }, 5_000)
      : undefined;
    return () => {
      active.current = false;
      generation.current += 1;
      sequence.current.profile += 1;
      sequence.current.status += 1;
      sequence.current.list += 1;
      unsubscribe?.();
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [api, refresh, refreshProfile, refreshStatus]);

  useEffect(() => {
    void refreshList();
  }, [query, refreshList]);

  const mutateProfile = useCallback(
    async (
      operation: () => Promise<PersonalMemoryResult<UserProfile>>,
      failure: string,
    ): Promise<PersonalMemoryResult<UserProfile>> => {
      if (!api) return { error: unavailable };
      if (pending.current.has("profile"))
        return { error: "A profile change is already saving." };
      pending.current.add("profile");
      const scope = generation.current;
      sequence.current.profile += 1;
      setSavingProfile(true);
      let result: PersonalMemoryResult<UserProfile>;
      try {
        result = await operation();
      } catch {
        result = { error: failure };
      }
      if (active.current && scope === generation.current) {
        sequence.current.profile += 1;
        if (!("error" in result)) {
          setProfile((previous) =>
            !previous || result.revision >= previous.revision
              ? result
              : previous,
          );
          setProfileError(null);
        } else if (result.conflict) void refreshProfile();
        setSavingProfile(false);
        setProfileLoading(false);
      }
      pending.current.delete("profile");
      return result;
    },
    [api, refreshProfile],
  );

  const saveProfile = useCallback(
    (input: UserProfileInput, expectedRevision?: number) =>
      mutateProfile(
        () =>
          api!.saveProfile({ ...input, expected_revision: expectedRevision }),
        "Could not save your profile. Try again.",
      ),
    [api, mutateProfile],
  );
  const resetProfile = useCallback(
    () =>
      mutateProfile(
        () => api!.resetProfile(),
        "Could not reset your profile. Try again.",
      ),
    [api, mutateProfile],
  );

  const saveSettings = useCallback(
    async (
      input: Omit<MemorySettings, "revision">,
      expectedRevision?: number,
    ): Promise<PersonalMemoryResult<MemorySettings>> => {
      if (!api) return { error: unavailable };
      if (pending.current.has("settings"))
        return { error: "Memory settings are already saving." };
      pending.current.add("settings");
      const scope = generation.current;
      sequence.current.status += 1;
      setSavingSettings(true);
      let result: PersonalMemoryResult<MemorySettings>;
      try {
        result = await api.saveSettings({
          ...input,
          expected_revision: expectedRevision,
        });
      } catch {
        result = { error: "Could not save memory settings. Try again." };
      }
      if (active.current && scope === generation.current) {
        sequence.current.status += 1;
        if (!("error" in result)) {
          setSettings((previous) =>
            !previous || result.revision >= previous.revision
              ? result
              : previous,
          );
          setMemoryError(null);
        }
        if (!("error" in result) || result.conflict) void refreshStatus();
        setSavingSettings(false);
        setMemoryLoading(false);
      }
      pending.current.delete("settings");
      return result;
    },
    [api, refreshStatus],
  );

  const mutateMemory = useCallback(
    async <T extends { deleted: boolean } | { cleared: boolean }>(
      operation: () => Promise<PersonalMemoryResult<T>>,
      failure: string,
      onSuccess: () => void,
    ): Promise<PersonalMemoryResult<T>> => {
      if (!api) return { error: unavailable };
      if (pending.current.has("memory"))
        return { error: "A memory change is already saving." };
      pending.current.add("memory");
      const scope = generation.current;
      sequence.current.list += 1;
      sequence.current.status += 1;
      setChangingMemory(true);
      let result: PersonalMemoryResult<T>;
      try {
        result = await operation();
      } catch {
        result = { error: failure };
      }
      if (
        !("error" in result) &&
        (("deleted" in result && !result.deleted) ||
          ("cleared" in result && !result.cleared))
      )
        result = { error: failure };
      if (active.current && scope === generation.current) {
        sequence.current.list += 1;
        sequence.current.status += 1;
        if (!("error" in result)) {
          onSuccess();
          void refreshList();
          void refreshStatus();
        }
        setChangingMemory(false);
      }
      pending.current.delete("memory");
      return result;
    },
    [api, refreshList, refreshStatus],
  );

  const deleteMemory = useCallback(
    (id: string) =>
      mutateMemory(
        () => api!.deleteMemory(id),
        "Could not delete memory. Try again.",
        () =>
          setMemories((previous) => previous.filter((row) => row.id !== id)),
      ),
    [api, mutateMemory],
  );
  const clearMemory = useCallback(
    () =>
      mutateMemory(
        () => api!.clearMemory(),
        "Could not clear conversation memory. Try again.",
        () => {
          setMemories([]);
          setMemoryStatus((previous) =>
            previous
              ? { ...previous, checkpoint: null, status: null }
              : previous,
          );
        },
      ),
    [api, mutateMemory],
  );

  return {
    available: Boolean(api),
    profile,
    settings,
    memoryStatus,
    memories,
    query,
    setQuery,
    profileError,
    memoryError,
    listError,
    profileLoading,
    memoryLoading,
    listLoading,
    savingProfile,
    savingSettings,
    changingMemory,
    refresh,
    refreshProfile,
    refreshStatus,
    refreshList,
    saveProfile,
    resetProfile,
    saveSettings,
    deleteMemory,
    clearMemory,
  };
}

export type PersonalMemoryState = ReturnType<typeof usePersonalMemory>;
