import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ConnectionApi,
  ConnectionSaveResult,
  ConnectionSettings,
} from "@shared/types";

import { Settings } from "./Settings";

describe("Settings themes", () => {
  afterEach(cleanup);

  it("shows six themes, identifies the current one, and selects a different palette", () => {
    const onSelect = vi.fn();
    render(<Settings activeTheme="blue" onSelect={onSelect} error={null} />);
    expect(screen.getByRole("heading", { name: "Themes" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Holographic Blue, current theme" }),
    ).toBeDisabled();
    expect(screen.getAllByRole("button", { name: /theme/ })).toHaveLength(6);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Apply Neon Green theme and restart IRA",
      }),
    );
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("green");
  });

  it("explains restart behavior and displays a recoverable save error", () => {
    render(
      <Settings
        activeTheme="blue"
        onSelect={vi.fn()}
        error="Could not save your theme. Please try again."
      />,
    );
    expect(
      screen.getByText(/choosing a theme restarts ira/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not save your theme",
    );
    expect(
      screen.getByRole("button", {
        name: "Apply Neon Green theme and restart IRA",
      }),
    ).toBeEnabled();
  });

  it("keeps themes as the default and lets people navigate to connection settings", () => {
    render(<Settings activeTheme="blue" onSelect={vi.fn()} error={null} />);
    expect(screen.getByRole("heading", { name: "Themes" })).toBeInTheDocument();
    const connectionTab = screen.getByRole("tab", {
      name: "Connection and Keys",
    });
    fireEvent.click(connectionTab);
    expect(connectionTab).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("heading", { name: "Connection and Keys" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Themes" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Personalization" }));
    expect(screen.getByRole("heading", { name: "Themes" })).toBeInTheDocument();
  });

  it("includes profile and memory tabs in the keyboard navigation", () => {
    render(<Settings activeTheme="blue" onSelect={vi.fn()} error={null} />);
    const first = screen.getByRole("tab", { name: "Personalization" });
    fireEvent.keyDown(first, { key: "End" });
    const memory = screen.getByRole("tab", { name: "Memory and Context" });
    expect(memory).toHaveFocus();
    expect(memory).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(memory, { key: "ArrowLeft" });
    const profile = screen.getByRole("tab", { name: "Your Profile" });
    expect(profile).toHaveFocus();
    expect(
      screen.getByRole("heading", { name: "Your Profile" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(profile, { key: "Home" });
    fireEvent.keyDown(first, { key: "ArrowLeft" });
    expect(memory).toHaveFocus();
  });
});

const freshSettings: ConnectionSettings = {
  configured: false,
  livekitUrl: "",
  hasGoogleApiKey: false,
  hasLivekitApiKey: false,
  hasLivekitApiSecret: false,
};
const savedSettings: ConnectionSettings = {
  configured: true,
  livekitUrl: "wss://saved-project.livekit.cloud",
  hasGoogleApiKey: true,
  hasLivekitApiKey: true,
  hasLivekitApiSecret: true,
};
const enteredKeys = {
  googleApiKey: "google-test-key",
  livekitUrl: "wss://new-project.livekit.cloud",
  livekitApiKey: "livekit-test-key",
  livekitApiSecret: "livekit-test-secret",
};

function openConnections(api?: ConnectionApi) {
  render(
    <Settings
      activeTheme="blue"
      onSelect={vi.fn()}
      error={null}
      connectionApi={api}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Connection and Keys" }));
}

function enterConnections() {
  fireEvent.change(screen.getByLabelText("Google API key"), {
    target: { value: enteredKeys.googleApiKey },
  });
  fireEvent.change(screen.getByLabelText("LiveKit URL"), {
    target: { value: enteredKeys.livekitUrl },
  });
  fireEvent.change(screen.getByLabelText("LiveKit API key"), {
    target: { value: enteredKeys.livekitApiKey },
  });
  fireEvent.change(screen.getByLabelText("LiveKit API secret"), {
    target: { value: enteredKeys.livekitApiSecret },
  });
}

describe("Settings connections", () => {
  afterEach(cleanup);

  it("loads saved metadata with empty masked secret fields", async () => {
    let completeLoad!: (settings: ConnectionSettings) => void;
    const api: ConnectionApi = {
      get: () =>
        new Promise((resolve) => {
          completeLoad = resolve;
        }),
      save: vi.fn(),
      onChanged: () => () => {},
    };
    openConnections(api);
    expect(screen.getByRole("status")).toHaveTextContent(
      /loading saved connection/i,
    );
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeDisabled();
    await act(async () => completeLoad(savedSettings));
    expect(
      screen.getByRole("heading", { name: "Google / Gemini LLM" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "LiveKit" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("LiveKit URL")).toHaveValue(
      "wss://saved-project.livekit.cloud",
    );
    for (const label of [
      "Google API key",
      "LiveKit API key",
      "LiveKit API secret",
    ]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("type", "password");
      expect(screen.getByLabelText(label)).toHaveValue("");
      expect(screen.getByLabelText(label)).toHaveAccessibleDescription(
        /saved.*leave blank/i,
      );
    }
    expect(screen.getByText("Keys saved")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeEnabled();
  });

  it("requires missing credentials and identifies the fields before calling save", async () => {
    const save = vi.fn();
    openConnections({
      get: async () => freshSettings,
      save,
      onChanged: () => () => {},
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save and validate" }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Google API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Google API key")).toHaveFocus();
    expect(screen.getByLabelText("Google API key")).toHaveAccessibleDescription(
      /enter your google api key/i,
    );
    expect(screen.getByLabelText("LiveKit URL")).toHaveAccessibleDescription(
      /enter your livekit/i,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/highlighted fields/i);
    fireEvent.change(screen.getByLabelText("Google API key"), {
      target: { value: "g" },
    });
    expect(screen.getByLabelText("Google API key")).toHaveFocus();
  });

  it("saves new credentials once, clears the secret inputs, and refreshes metadata", async () => {
    let completeSave!: (result: ConnectionSaveResult) => void;
    let saveCount = 0;
    let received: unknown;
    let didSave = false;
    const get = vi.fn(async () =>
      didSave
        ? { ...savedSettings, livekitUrl: enteredKeys.livekitUrl }
        : freshSettings,
    );
    const api: ConnectionApi = {
      get,
      save: async (input) => {
        saveCount += 1;
        received = input;
        return new Promise((resolve) => {
          completeSave = resolve;
        });
      },
      onChanged: () => () => {},
    };
    openConnections(api);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    enterConnections();
    fireEvent.click(screen.getByRole("button", { name: "Save and validate" }));
    expect(
      screen.getByRole("button", { name: /validating and saving/i }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Google API key")).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "Connection and Keys" }));
    expect(saveCount).toBe(1);
    expect(received).toEqual(enteredKeys);
    didSave = true;
    await act(async () =>
      completeSave({
        settings: { ...savedSettings, livekitUrl: enteredKeys.livekitUrl },
      }),
    );
    expect(
      await screen.findByText(/connection saved and validated/i),
    ).toBeInTheDocument();
    for (const label of [
      "Google API key",
      "LiveKit API key",
      "LiveKit API secret",
    ]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    expect(screen.getByLabelText("LiveKit URL")).toHaveValue(
      enteredKeys.livekitUrl,
    );
    expect(screen.getByText("Keys saved")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("allows a saved user to validate without entering secrets again", async () => {
    let received: unknown;
    openConnections({
      get: async () => savedSettings,
      save: async (input) => {
        received = input;
        return { settings: savedSettings };
      },
      onChanged: () => () => {},
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save and validate" }));
    expect(
      await screen.findByText(/connection saved and validated/i),
    ).toBeInTheDocument();
    expect(received).toEqual({
      googleApiKey: "",
      livekitUrl: "wss://saved-project.livekit.cloud",
      livekitApiKey: "",
      livekitApiSecret: "",
    });
  });

  it("keeps entered values and focuses a provider field error for correction", async () => {
    openConnections({
      get: async () => freshSettings,
      save: async () => ({
        error: "Google rejected this API key. Check the key and try again.",
        field: "googleApiKey",
      }),
      onChanged: () => () => {},
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    enterConnections();
    fireEvent.click(screen.getByRole("button", { name: "Save and validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /google rejected/i,
    );
    expect(screen.getByLabelText("Google API key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText("Google API key")).toHaveAccessibleDescription(
      /google rejected/i,
    );
    expect(screen.getByLabelText("Google API key")).toHaveValue(
      "google-test-key",
    );
    expect(screen.getByLabelText("Google API key")).toHaveFocus();
    fireEvent.change(screen.getByLabelText("Google API key"), {
      target: { value: "corrected-test-key" },
    });
    expect(screen.getByLabelText("Google API key")).toHaveAttribute(
      "aria-invalid",
      "false",
    );
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeEnabled();
  });

  it("offers a retry after metadata loading fails", async () => {
    const get = vi
      .fn()
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValue(savedSettings);
    openConnections({ get, save: vi.fn(), onChanged: () => () => {} });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not load saved connection/i,
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(screen.getByLabelText("LiveKit URL")).toHaveValue(
        "wss://saved-project.livekit.cloud",
      ),
    );
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeEnabled();
  });

  it("does not expose a thrown save error and permits a retry", async () => {
    openConnections({
      get: async () => freshSettings,
      save: async () => {
        throw new Error("internal google-test-key livekit-test-secret");
      },
      onChanged: () => () => {},
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    enterConnections();
    fireEvent.click(screen.getByRole("button", { name: "Save and validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not save your connection/i,
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /internal|google-test-key|livekit-test-secret/,
    );
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeEnabled();
  });

  it("refreshes saved metadata on change without overwriting a URL being edited", async () => {
    let changed!: () => void;
    let settings = savedSettings;
    const unsubscribe = vi.fn();
    openConnections({
      get: async () => settings,
      save: vi.fn(),
      onChanged: (listener) => {
        changed = listener;
        return unsubscribe;
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save and validate" }),
      ).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("LiveKit URL"), {
      target: { value: "wss://edited-project.livekit.cloud" },
    });
    settings = {
      ...freshSettings,
      livekitUrl: "wss://other-project.livekit.cloud",
    };
    await act(async () => changed());
    expect(screen.getByLabelText("LiveKit URL")).toHaveValue(
      "wss://edited-project.livekit.cloud",
    );
    expect(screen.getByLabelText("Google API key")).toHaveAccessibleDescription(
      /enter your google/i,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Personalization" }));
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("explains that connection saving is available in the desktop app", () => {
    openConnections();
    expect(screen.getByText(/open ira.*desktop.*save/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save and validate" }),
    ).toBeDisabled();
  });

  it("supports keyboard navigation between Settings tabs", () => {
    render(<Settings activeTheme="blue" onSelect={vi.fn()} error={null} />);
    const personalization = screen.getByRole("tab", {
      name: "Personalization",
    });
    personalization.focus();
    fireEvent.keyDown(personalization, { key: "ArrowRight" });
    const connection = screen.getByRole("tab", { name: "Connection and Keys" });
    expect(connection).toHaveFocus();
    expect(connection).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(connection, { key: "Home" });
    expect(personalization).toHaveFocus();
    expect(screen.getByRole("heading", { name: "Themes" })).toBeInTheDocument();
  });
});
