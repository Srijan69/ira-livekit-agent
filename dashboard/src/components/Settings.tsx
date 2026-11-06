import {
  ArrowUpRight,
  Check,
  Database,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Palette,
  Radio,
  RotateCw,
  UserRound,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import type {
  ConnectionApi,
  ConnectionInput,
  ConnectionSettings,
  PersonalMemoryApi,
} from "@shared/types";

import { THEME_ASSETS } from "@/lib/themeAssets";
import { getTheme, THEMES, type ThemeId } from "@/lib/themes";
import {
  usePersonalMemory,
  type PersonalMemoryState,
} from "@/hooks/usePersonalMemory";
import { MemorySettingsPanel, ProfileSettings } from "./PersonalMemorySettings";

const settingsSections = [
  { id: "personalization", label: "Personalization", icon: Palette },
  { id: "connection", label: "Connection and Keys", icon: KeyRound },
  { id: "profile", label: "Your Profile", icon: UserRound },
  { id: "memory", label: "Memory and Context", icon: Database },
] as const;
type SettingsTab = (typeof settingsSections)[number]["id"];
type FieldErrors = Partial<Record<keyof ConnectionInput, string>>;
const emptyConnection: ConnectionInput = {
  googleApiKey: "",
  livekitUrl: "",
  livekitApiKey: "",
  livekitApiSecret: "",
};
const connectionLabels: Record<keyof ConnectionInput, string> = {
  googleApiKey: "Google API key",
  livekitUrl: "LiveKit URL",
  livekitApiKey: "LiveKit API key",
  livekitApiSecret: "LiveKit API secret",
};

function ConnectionField({
  field,
  value,
  saved = false,
  error,
  onChange,
}: {
  field: keyof ConnectionInput;
  value: string;
  saved?: boolean;
  error?: string;
  onChange: (field: keyof ConnectionInput, value: string) => void;
}) {
  const isUrl = field === "livekitUrl";
  const id = `connection-${field}`;
  const label = connectionLabels[field];
  return (
    <div className="connection-field">
      <div className="connection-field-label">
        <label htmlFor={id}>{label}</label>
        {!isUrl && (
          <span
            className={`connection-key-state ${saved ? "is-saved" : ""}`}
            aria-hidden="true"
          >
            {saved ? (
              <>
                <Check size={11} /> Saved
              </>
            ) : (
              "Not set"
            )}
          </span>
        )}
      </div>
      <input
        id={id}
        name={field}
        type={isUrl ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(field, event.target.value)}
        placeholder={
          isUrl
            ? "wss://your-project.livekit.cloud"
            : saved
              ? "Leave blank to keep saved key"
              : `Enter ${label.toLowerCase()}`
        }
        autoComplete={isUrl ? "off" : "new-password"}
        spellCheck={false}
        autoCapitalize="none"
        maxLength={field === "livekitApiSecret" || isUrl ? 2048 : 512}
        aria-required={!saved}
        aria-invalid={Boolean(error)}
        aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`}
      />
      <p id={`${id}-help`} className="connection-field-help">
        {isUrl
          ? "Use the WebSocket URL from your LiveKit project, starting with wss://."
          : saved
            ? `${label} saved. Leave blank to keep it, or enter a replacement.`
            : `Enter your ${label.toLowerCase()} to connect IRA.`}
      </p>
      {error && (
        <p id={`${id}-error`} className="connection-field-error">
          {error}
        </p>
      )}
    </div>
  );
}

function ConnectionPanel({ api }: { api?: ConnectionApi }) {
  const [settings, setSettings] = useState<ConnectionSettings | null>(null);
  const [input, setInput] = useState<ConnectionInput>(emptyConnection);
  const [loading, setLoading] = useState(Boolean(api));
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const mounted = useRef(false);
  const savingRef = useRef(false);
  const loadSequence = useRef(0);
  const urlEdited = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const focusAfterSave = useRef<keyof ConnectionInput | null>(null);

  const loadSettings = useCallback(async () => {
    if (!api) return;
    const sequence = ++loadSequence.current;
    try {
      const next = await api.get();
      if (!mounted.current || sequence !== loadSequence.current) return;
      setSettings(next);
      setLoadError(next.error || null);
      if (!urlEdited.current)
        setInput((current) => ({ ...current, livekitUrl: next.livekitUrl }));
    } catch {
      if (mounted.current && sequence === loadSequence.current) {
        setLoadError("Could not load saved connection settings. Try again.");
      }
    } finally {
      if (mounted.current && sequence === loadSequence.current)
        setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    mounted.current = true;
    setLoading(Boolean(api));
    void loadSettings();
    const unsubscribe = api?.onChanged(() => {
      void loadSettings();
    });
    return () => {
      mounted.current = false;
      loadSequence.current += 1;
      unsubscribe?.();
    };
  }, [api, loadSettings]);

  function focusField(field: keyof ConnectionInput) {
    const element = form.current?.elements.namedItem(field);
    if (element instanceof HTMLInputElement) element.focus();
  }

  function changeField(field: keyof ConnectionInput, value: string) {
    if (field === "livekitUrl") urlEdited.current = true;
    setInput((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
    setSaveError(null);
    setSuccess(false);
  }

  async function saveConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!api || loading || !settings || savingRef.current) return;
    const errors: FieldErrors = {};
    if (!input.googleApiKey.trim() && !settings.hasGoogleApiKey)
      errors.googleApiKey = "Enter your Google API key from Google AI Studio.";
    if (!input.livekitUrl.trim())
      errors.livekitUrl = "Enter your LiveKit project WebSocket URL.";
    if (!input.livekitApiKey.trim() && !settings.hasLivekitApiKey)
      errors.livekitApiKey = "Enter your LiveKit project API key.";
    if (!input.livekitApiSecret.trim() && !settings.hasLivekitApiSecret)
      errors.livekitApiSecret = "Enter your LiveKit project API secret.";
    setSuccess(false);
    setFieldErrors(errors);
    const firstError = Object.keys(errors)[0] as
      keyof ConnectionInput | undefined;
    if (firstError) {
      setSaveError("Check the highlighted fields before saving.");
      focusField(firstError);
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.save({ ...input });
      if (!mounted.current) return;
      if ("error" in result) {
        setSaveError(result.error);
        if (result.field) {
          setFieldErrors({ [result.field]: result.error });
          focusAfterSave.current = result.field;
        }
      } else {
        loadSequence.current += 1;
        urlEdited.current = false;
        setSettings(result.settings);
        setInput({
          ...emptyConnection,
          livekitUrl: result.settings.livekitUrl,
        });
        setLoadError(result.settings.error || null);
        setSuccess(true);
        void loadSettings();
      }
    } catch {
      if (mounted.current)
        setSaveError("Could not save your connection. Please try again.");
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  // The input must be enabled before focusing a provider error returned by save.
  useEffect(() => {
    if (!saving && focusAfterSave.current) {
      focusField(focusAfterSave.current);
      focusAfterSave.current = null;
    }
  }, [saving]);

  const disabled = !api || loading || saving || !settings;
  const field = (name: keyof ConnectionInput, saved?: boolean) => (
    <ConnectionField
      field={name}
      value={input[name]}
      saved={saved}
      error={fieldErrors[name]}
      onChange={changeField}
    />
  );
  return (
    <div className="connection-panel">
      <div className="connection-heading">
        <div>
          <h2 id="connection-title" className="text-[20px] font-semibold">
            Connection and Keys
          </h2>
          <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
            Connect IRA to your Gemini model and LiveKit project.
          </p>
        </div>
        {settings && (
          <span
            className={`connection-status ${settings.configured ? "is-ready" : ""}`}
          >
            {settings.configured ? (
              <>
                <Check size={13} /> Keys saved
              </>
            ) : (
              "Setup required"
            )}
          </span>
        )}
      </div>
      {!api && (
        <p className="connection-notice">
          Open IRA in the desktop app to save and validate your connection keys.
        </p>
      )}
      {loading && (
        <p className="connection-notice" role="status">
          <LoaderCircle size={15} className="connection-spinner" /> Loading
          saved connection settings…
        </p>
      )}
      {loadError && (
        <div className="connection-feedback is-error">
          <p role="alert">{loadError}</p>
          <button
            type="button"
            className="connection-retry"
            disabled={loading || saving}
            onClick={() => {
              setLoading(true);
              setLoadError(null);
              void loadSettings();
            }}
          >
            Try again
          </button>
        </div>
      )}
      <form
        ref={form}
        aria-labelledby="connection-title"
        onSubmit={saveConnection}
        noValidate
      >
        <fieldset
          className="connection-grid"
          disabled={disabled}
          aria-busy={loading || saving}
        >
          <legend className="sr-only">Provider credentials</legend>
          <section
            className="connection-card"
            aria-labelledby="google-connection-title"
          >
            <div className="connection-card-heading">
              <span className="connection-provider-icon">
                <KeyRound size={19} />
              </span>
              <div>
                <h3 id="google-connection-title">Google / Gemini LLM</h3>
                <p>IRA’s voice and reasoning model</p>
              </div>
            </div>
            {field("googleApiKey", settings?.hasGoogleApiKey)}
            <p className="connection-provider-note">
              Find your key in Google AI Studio. Your key is validated before it
              is saved.
            </p>
          </section>
          <section
            className="connection-card"
            aria-labelledby="livekit-connection-title"
          >
            <div className="connection-card-heading">
              <span className="connection-provider-icon">
                <Radio size={19} />
              </span>
              <div>
                <h3 id="livekit-connection-title">LiveKit</h3>
                <p>Your real-time voice connection</p>
              </div>
            </div>
            {field("livekitUrl")}
            {field("livekitApiKey", settings?.hasLivekitApiKey)}
            {field("livekitApiSecret", settings?.hasLivekitApiSecret)}
          </section>
        </fieldset>
        {saveError && (
          <p role="alert" className="connection-feedback is-error">
            {saveError}
          </p>
        )}
        {success && (
          <p role="status" className="connection-feedback is-success">
            <Check size={15} /> Connection saved and validated. IRA is
            reconnecting.
          </p>
        )}
        <div className="connection-save-row">
          <p className="connection-privacy-note">
            <LockKeyhole size={15} />
            <span>
              Keys are stored securely on this device. Saved secrets stay
              hidden.
            </span>
          </p>
          <button
            type="submit"
            className="connection-save-button"
            disabled={disabled}
          >
            {saving ? (
              <>
                <LoaderCircle size={15} className="connection-spinner" />{" "}
                Validating and saving…
              </>
            ) : (
              <>
                <Check size={15} /> Save and validate
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

export function Settings({
  activeTheme,
  onSelect,
  error,
  connectionApi = window.ira?.connection,
  personalMemoryApi = window.ira?.personalMemory,
  personalMemory,
}: {
  activeTheme: ThemeId;
  onSelect: (id: ThemeId) => void;
  error: string | null;
  connectionApi?: ConnectionApi;
  personalMemoryApi?: PersonalMemoryApi;
  personalMemory?: PersonalMemoryState;
}) {
  const current = getTheme(activeTheme);
  const [tab, setTab] = useState<SettingsTab>("personalization");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const ownMemory = usePersonalMemory(
    personalMemory ? null : personalMemoryApi,
  );
  const memory = personalMemory ?? ownMemory;
  const section = settingsSections.find((section) => section.id === tab)!;

  function navigateTabs(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    let next: number;
    if (event.key === "ArrowRight")
      next = (index + 1) % settingsSections.length;
    else if (event.key === "ArrowLeft")
      next = (index + settingsSections.length - 1) % settingsSections.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = settingsSections.length - 1;
    else return;
    event.preventDefault();
    setTab(settingsSections[next].id);
    tabs.current[next]?.focus();
  }

  return (
    <section
      className="settings-page panel min-h-0 flex-1 overflow-y-auto"
      aria-labelledby="settings-title"
    >
      <div className="settings-content">
        <header className="settings-header">
          <div>
            <p className="label-caps">Make yourself at home</p>
            <h1
              id="settings-title"
              className="mt-2 text-[28px] font-semibold tracking-tight"
            >
              Settings
            </h1>
          </div>
          <span className="settings-section-tag">
            <section.icon size={15} /> {section.label}
          </span>
        </header>

        <div
          className="settings-tabs"
          role="tablist"
          aria-label="Settings sections"
        >
          {settingsSections.map(({ id, label, icon: Icon }, index) => (
            <button
              key={id}
              ref={(element) => {
                tabs.current[index] = element;
              }}
              id={`settings-tab-${id}`}
              type="button"
              role="tab"
              aria-selected={tab === id}
              aria-controls={`settings-panel-${id}`}
              tabIndex={tab === id ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={(event) => navigateTabs(event, index)}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>

        {tab === "connection" ? (
          <div
            id="settings-panel-connection"
            role="tabpanel"
            aria-labelledby="settings-tab-connection"
          >
            <ConnectionPanel api={connectionApi} />
          </div>
        ) : tab === "personalization" ? (
          <div
            id="settings-panel-personalization"
            role="tabpanel"
            aria-labelledby="settings-tab-personalization"
          >
            <div className="themes-heading">
              <div>
                <h2 className="text-[20px] font-semibold">Themes</h2>
                <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
                  Same IRA. A whole new atmosphere.
                </p>
              </div>
              <span className="theme-current-label">
                <span style={{ background: current.accent }} />
                {current.name} active
              </span>
            </div>

            <div className="theme-grid" aria-label="Choose a color theme">
              {THEMES.map((theme) => {
                const active = activeTheme === theme.id;
                return (
                  <button
                    key={theme.id}
                    type="button"
                    className={`theme-card ${active ? "is-active" : ""}`}
                    style={{ "--card-accent": theme.accent } as CSSProperties}
                    disabled={active}
                    aria-label={
                      active
                        ? `${theme.name}, current theme`
                        : `Apply ${theme.name} theme and restart IRA`
                    }
                    onClick={() => onSelect(theme.id)}
                  >
                    <div className="theme-card-image">
                      <img
                        src={THEME_ASSETS[theme.id].preview}
                        alt=""
                        width={600}
                        height={400}
                        draggable={false}
                      />
                      <span className="theme-card-badge">
                        {active ? (
                          <>
                            <Check size={12} /> Current theme
                          </>
                        ) : (
                          <>
                            <RotateCw size={12} /> Apply & restart
                          </>
                        )}
                      </span>
                    </div>
                    <div className="theme-card-details">
                      <div className="flex items-center justify-between gap-2">
                        <h3 className="text-[13px] font-semibold">
                          {theme.name}
                        </h3>
                        <span className="theme-swatches" aria-hidden>
                          <span style={{ background: theme.accent }} />
                          <span style={{ background: theme.secondary }} />
                          <span style={{ background: theme.deep }} />
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-dim">
                        {theme.description}
                      </p>
                      <span className="theme-card-action" aria-hidden>
                        {active ? "You're right at home" : "Make it yours"}
                        {!active && <ArrowUpRight size={13} />}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>

            {error && (
              <p
                role="alert"
                className="mt-4 rounded-xl border border-warn/30 bg-warn/10 px-4 py-3 text-[12px] text-warn"
              >
                {error}
              </p>
            )}

            <footer className="theme-restart-note">
              <RotateCw size={15} className="shrink-0 text-cyan" />
              <p>
                Choosing a theme restarts IRA. Your current conversation will
                disconnect, and your new look will be ready when IRA returns.
              </p>
            </footer>
          </div>
        ) : null}
        <div
          id="settings-panel-profile"
          role="tabpanel"
          aria-labelledby="settings-tab-profile"
          hidden={tab !== "profile"}
        >
          <ProfileSettings memory={memory} />
        </div>
        <div
          id="settings-panel-memory"
          role="tabpanel"
          aria-labelledby="settings-tab-memory"
          hidden={tab !== "memory"}
        >
          <MemorySettingsPanel memory={memory} />
        </div>
      </div>
    </section>
  );
}
