import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  PersonalMemoryApi,
  UserProfile,
  UserProfileInput,
  ContextMemoryStatus,
} from "@shared/types";

import { usePersonalMemory } from "@/hooks/usePersonalMemory";
import { Settings } from "./Settings";
import { Sidebar } from "./Sidebar";

const emptyProfile: UserProfile = {
  name: "",
  age: null,
  occupation: "",
  preferences: "",
  revision: 0,
  updated_at: null,
};
const initialProfile: UserProfile = {
  ...emptyProfile,
  name: "Ada Lovelace",
  occupation: "Engineer",
  revision: 2,
};
const initialSettings = {
  enabled: true,
  context_budget: 16000,
  summary_trigger: 12000,
  recent_turns: 12,
  summarizer_model: "gemini-3.8-flash",
  revision: 0,
};
function fixture() {
  let profile = initialProfile;
  let settings = initialSettings;
  let memories = [
    {
      id: "fact-1",
      kind: "fact",
      content: "Enjoys astronomy",
      created_at: "2026-11-06T10:00:00Z",
      source: "explicit",
    },
  ];
  let changed = () => {};
  let state: ContextMemoryStatus["state"] | null = "collecting";
  const checkpoint = {
    id: "cp-1",
    conversation_id: "conversation-1",
    through_seq: 12,
    memory_revision: 0,
    created_at: "2026-11-06T10:00:00Z",
    summary: {
      overview: "Discussed a Mars project.",
      facts: [],
      decisions: [{ content: "Use metric units", source_item_ids: ["turn-1"] }],
      open_threads: [
        { content: "Choose a launch date", source_item_ids: ["turn-2"] },
      ],
      outcomes: [],
      questions: [],
    },
  };
  const api: PersonalMemoryApi = {
    profile: async () => profile,
    saveProfile: async (input) => {
      profile = { ...profile, ...input, revision: profile.revision + 1 };
      changed();
      return profile;
    },
    resetProfile: async () => {
      profile = { ...emptyProfile, revision: profile.revision + 1 };
      changed();
      return profile;
    },
    settings: async () => settings,
    saveSettings: async (input) => {
      settings = { ...settings, ...input, revision: settings.revision + 1 };
      changed();
      return settings;
    },
    status: async () => ({
      settings,
      memory_revision: 0,
      checkpoint: memories.length ? checkpoint : null,
      status: state ? { state } : null,
    }),
    list: async (query) => ({
      memories: memories.filter(
        (row) =>
          !query || row.content.toLowerCase().includes(query.toLowerCase()),
      ),
    }),
    deleteMemory: async (id) => {
      memories = memories.filter((row) => row.id !== id);
      changed();
      return { deleted: true };
    },
    clearMemory: async () => {
      memories = [];
      changed();
      return { cleared: true };
    },
    onChanged: (listener) => {
      changed = listener;
      return () => {};
    },
  };
  return {
    api,
    updateProfile: (next: UserProfile) => {
      profile = next;
      changed();
    },
    setState: (next: typeof state) => {
      state = next;
      changed();
    },
  };
}
function Harness({ api }: { api?: PersonalMemoryApi }) {
  const personalMemory = usePersonalMemory(api ?? null);
  return (
    <>
      <Sidebar
        active="settings"
        onSelect={() => {}}
        profile={personalMemory.profile}
      />
      <Settings
        activeTheme="blue"
        onSelect={() => {}}
        error={null}
        personalMemory={personalMemory}
      />
    </>
  );
}
async function openProfile(api?: PersonalMemoryApi) {
  render(<Harness api={api} />);
  fireEvent.click(screen.getByRole("tab", { name: "Your Profile" }));
  if (api)
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save profile" }),
      ).toBeEnabled(),
    );
}
async function openMemory(api?: PersonalMemoryApi) {
  render(<Harness api={api} />);
  fireEvent.click(screen.getByRole("tab", { name: "Memory and Context" }));
  if (api)
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save memory settings" }),
      ).toBeEnabled(),
    );
}
afterEach(cleanup);

describe("Your Profile", () => {
  it("saves optional fields and updates the sidebar from the durable response", async () => {
    const { api } = fixture();
    let received:
      (UserProfileInput & { expected_revision?: number }) | undefined;
    const save = api.saveProfile;
    api.saveProfile = async (input) => {
      received = input;
      return save(input);
    };
    await openProfile(api);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Grace Hopper" },
    });
    fireEvent.change(screen.getByLabelText("Age (optional)"), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByLabelText("Occupation"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Preferences"), {
      target: { value: "Keep examples practical" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await screen.findByText(/profile saved/i);
    expect(received).toEqual({
      name: "Grace Hopper",
      age: 0,
      occupation: "",
      preferences: "Keep examples practical",
      expected_revision: 2,
    });
    expect(
      within(screen.getByRole("navigation")).getByText("Grace Hopper"),
    ).toBeVisible();
    expect(
      within(screen.getByRole("navigation")).getByText("GH"),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Age (optional)"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(received?.age).toBeNull());
  });

  it.each(["-1", "131", "4.5"])(
    "rejects invalid age %s while preserving the form",
    async (age) => {
      const { api } = fixture();
      const save = vi.fn(api.saveProfile);
      api.saveProfile = save;
      await openProfile(api);
      fireEvent.change(screen.getByLabelText("Age (optional)"), {
        target: { value: age },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
      expect(screen.getByRole("alert")).toHaveTextContent(
        /whole number.*0.*130/i,
      );
      expect(screen.getByLabelText("Age (optional)")).toHaveFocus();
      expect(screen.getByLabelText("Name")).toHaveValue("Ada Lovelace");
      expect(save).not.toHaveBeenCalled();
    },
  );

  it("retains an unsaved edit across refresh and revision conflict until reload is chosen", async () => {
    const { api, updateProfile } = fixture();
    let revision: number | undefined;
    api.saveProfile = async (input) => {
      revision = input.expected_revision;
      return { error: "Profile changed elsewhere.", conflict: true };
    };
    await openProfile(api);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Unsaved name" },
    });
    await act(async () =>
      updateProfile({
        ...initialProfile,
        name: "Voice correction",
        revision: 3,
      }),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Unsaved name");
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /changed elsewhere.*edits.*kept/i,
    );
    expect(revision).toBe(2);
    expect(screen.getByLabelText("Name")).toHaveValue("Unsaved name");
    fireEvent.click(
      screen.getByRole("button", { name: "Reload saved profile" }),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Voice correction");
  });

  it("preserves input on failed save and failed reset, then confirms a separate reset", async () => {
    const { api } = fixture();
    const reset = api.resetProfile;
    api.saveProfile = async () => {
      throw new Error("private transport detail");
    };
    api.resetProfile = async () => ({
      error: "Could not reset profile. Try again.",
    });
    await openProfile(api);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Kept edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not save.*try again/i,
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      "private transport detail",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reset profile" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Kept edit");
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm reset profile" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/could not reset/i),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Kept edit");
    api.resetProfile = reset;
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm reset profile" }),
    );
    await screen.findByText(/profile reset/i);
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("Age (optional)")).toHaveValue(null);
    expect(
      within(screen.getByRole("navigation")).getByText("Your profile"),
    ).toBeVisible();
    expect(screen.queryByText("Sai Srijan")).not.toBeInTheDocument();
  });

  it("keeps fresh browser profile empty and explains desktop availability", async () => {
    await openProfile();
    expect(screen.getByText(/open ira.*desktop app.*profile/i)).toBeVisible();
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save profile" })).toBeDisabled();
    expect(screen.queryByText("Sai Srijan")).not.toBeInTheDocument();
  });
});

describe("Memory and Context", () => {
  it("marks status unavailable when a later bridge read fails, retaining the saved summary", async () => {
    const { api, setState } = fixture();
    await act(async () => setState("ready"));
    const status = api.status;
    await openMemory(api);
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/provider.*confirmed/i);
    api.status = async () => ({
      error: "Memory bridge unavailable. Try again.",
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /bridge unavailable/i,
    );
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/session status unavailable/i);
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).not.toHaveTextContent(/provider.*confirmed/i);
    expect(screen.getByText("Discussed a Mars project.")).toBeVisible();
    api.status = status;
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() =>
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/provider.*confirmed/i);
  });

  it("shows saved summary without claiming provider readiness, then distinguishes restored from ready", async () => {
    const { api, setState } = fixture();
    await openMemory(api);
    expect(screen.getByText("Discussed a Mars project.")).toBeVisible();
    expect(screen.getByText("Choose a launch date")).toBeVisible();
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/collecting/i);
    await act(async () => setState("restored"));
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/context restored.*waiting.*provider/i);
    await act(async () => setState("ready"));
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/provider.*confirmed/i);
    await act(async () => setState(null));
    expect(
      screen.getByRole("status", { name: "Context memory status" }),
    ).toHaveTextContent(/session status unavailable/i);
  });

  it("renders the backend waiting_to_compact state and handles unknown states", async () => {
    const { api, setState } = fixture();
    await openMemory(api);
    await act(async () => setState("waiting_to_compact" as ContextMemoryStatus["state"]));
    expect(screen.getByRole("status", { name: "Context memory status" })).toHaveTextContent(/waiting to compact/i);
    await act(async () => setState("future-state" as ContextMemoryStatus["state"]));
    expect(screen.getByRole("status", { name: "Context memory status" })).toHaveTextContent(/session status unavailable/i);
  });

  it("saves automatic memory and validated advanced settings with a revision", async () => {
    const { api } = fixture();
    let received: unknown;
    const save = api.saveSettings;
    api.saveSettings = async (input) => {
      received = input;
      return save(input);
    };
    await openMemory(api);
    expect(
      screen.getByRole("checkbox", { name: "Automatic memory" }),
    ).toBeChecked();
    fireEvent.click(screen.getByText("Advanced context settings"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Automatic memory" }));
    fireEvent.change(
      screen.getByLabelText("Application context budget (tokens)"),
      { target: { value: "20000" } },
    );
    fireEvent.change(screen.getByLabelText("Summary trigger (tokens)"), {
      target: { value: "15000" },
    });
    fireEvent.change(screen.getByLabelText("Recent turns to retain"), {
      target: { value: "16" },
    });
    fireEvent.change(screen.getByLabelText("Summarizer model"), {
      target: { value: "gemini-test-model" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save memory settings" }),
    );
    await screen.findByText(/memory settings saved/i);
    expect(received).toEqual({
      enabled: false,
      context_budget: 20000,
      summary_trigger: 15000,
      recent_turns: 16,
      summarizer_model: "gemini-test-model",
      expected_revision: 0,
    });
    expect(screen.getByText(/configured google api key/i)).toBeVisible();
  });

  it("rejects a trigger too near the budget and retains settings on save conflict", async () => {
    const { api } = fixture();
    api.saveSettings = async () => ({
      error: "Memory settings changed elsewhere.",
      conflict: true,
    });
    await openMemory(api);
    fireEvent.click(screen.getByText("Advanced context settings"));
    fireEvent.change(screen.getByLabelText("Summary trigger (tokens)"), {
      target: { value: "15900" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save memory settings" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/2,048.*budget/i);
    fireEvent.change(screen.getByLabelText("Summary trigger (tokens)"), {
      target: { value: "13000" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Save memory settings" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /changed elsewhere.*edits.*kept/i,
    );
    expect(screen.getByLabelText("Summary trigger (tokens)")).toHaveValue(
      13000,
    );
  });

  it("searches and deletes memories, then clears conversation memory while preserving profile", async () => {
    const { api } = fixture();
    await openMemory(api);
    expect(screen.getByText("Enjoys astronomy")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Search memories"), {
      target: { value: "unknown" },
    });
    await screen.findByText(/no memories match/i);
    fireEvent.change(screen.getByLabelText("Search memories"), {
      target: { value: "astronomy" },
    });
    await screen.findByText("Enjoys astronomy");
    fireEvent.click(
      screen.getByRole("button", { name: "Delete memory: Enjoys astronomy" }),
    );
    await waitFor(() =>
      expect(screen.queryByText("Enjoys astronomy")).not.toBeInTheDocument(),
    );
    expect(
      within(screen.getByRole("navigation")).getByText("Ada Lovelace"),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Clear conversation memory" }),
    );
    expect(
      screen.getByRole("button", { name: "Confirm clear conversation memory" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm clear conversation memory" }),
    );
    await screen.findByText(/conversation memory cleared/i);
    expect(
      within(screen.getByRole("navigation")).getByText("Ada Lovelace"),
    ).toBeVisible();
    expect(
      screen.queryByText("Discussed a Mars project."),
    ).not.toBeInTheDocument();
  });

  it("keeps failed deletion visible and offers recovery", async () => {
    const { api } = fixture();
    api.deleteMemory = async () => ({
      error: "Could not delete memory. Try again.",
    });
    await openMemory(api);
    fireEvent.click(
      screen.getByRole("button", { name: "Delete memory: Enjoys astronomy" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not delete/i,
    );
    expect(screen.getByText("Enjoys astronomy")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Delete memory: Enjoys astronomy" }),
    ).toBeEnabled();
  });

  it("keeps the summary and facts when clearing fails", async () => {
    const { api } = fixture();
    api.clearMemory = async () => ({
      error: "Could not clear conversation memory. Try again.",
    });
    await openMemory(api);
    fireEvent.click(
      screen.getByRole("button", { name: "Clear conversation memory" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm clear conversation memory" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not clear/i,
    );
    expect(screen.getByText("Enjoys astronomy")).toBeVisible();
    expect(screen.getByText("Discussed a Mars project.")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Confirm clear conversation memory" }),
    ).toBeEnabled();
  });
});
