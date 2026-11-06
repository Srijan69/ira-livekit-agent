import { createHmac, randomUUID } from "node:crypto";
import { mkdir, open, readFile, rename, rm } from "node:fs/promises";
import { dirname } from "node:path";

import type { ConnectionInput, ConnectionSaveResult, ConnectionSettings } from "../shared/types";

export type ConnectionValidationError = { error: string; field?: keyof ConnectionInput };
export type ConnectionEncryption = {
  isEncryptionAvailable(): boolean;
  getSelectedStorageBackend?(): string;
  encryptString(value: string): Buffer;
  decryptString(value: Buffer): string;
};
export type ConnectionStoreOptions = {
  path: string;
  encryption: ConnectionEncryption;
  validate?: (candidate: ConnectionInput) => Promise<ConnectionValidationError | null>;
};
export type ConnectionStore = {
  get(): Promise<ConnectionSettings>;
  save(input: unknown): Promise<ConnectionSaveResult>;
  childEnv(): Promise<NodeJS.ProcessEnv>;
};

const fields = ["googleApiKey", "livekitUrl", "livekitApiKey", "livekitApiSecret"] as const;
const secretFields = ["googleApiKey", "livekitApiKey", "livekitApiSecret"] as const;
const storageUnavailable = "Secure credential storage is unavailable. Enable your operating system key store and restart IRA.";
const unreadableSettings = "Saved connection settings could not be read securely. Re-enter your keys to replace them.";
const validationFailed = "Connection validation failed. Check your keys and server URL, then try again.";
const emptyEnvironment = {
  GOOGLE_API_KEY: "",
  LIVEKIT_URL: "",
  LIVEKIT_API_KEY: "",
  LIVEKIT_API_SECRET: "",
};

function normalizeInput(input: unknown, saved: ConnectionInput | null = null): { candidate: ConnectionInput } | ConnectionValidationError {
  if (!input || typeof input !== "object" || Array.isArray(input)) return { error: "Enter your Google and LiveKit connection settings." };
  const values = input as Record<string, unknown>;
  const candidate = {} as ConnectionInput;
  for (const field of secretFields) {
    if (typeof values[field] !== "string") return { error: "Enter a valid API key or secret.", field };
    const value = (values[field] as string).trim() || saved?.[field] || "";
    const limit = field === "livekitApiSecret" ? 2048 : 512;
    if (!value || value.length > limit || !/^[\x21-\x7e]+$/.test(value)) {
      return { error: "Enter an API key or secret without spaces or control characters.", field };
    }
    candidate[field] = value;
  }
  if (typeof values.livekitUrl !== "string") return { error: "Enter a valid LiveKit server URL.", field: "livekitUrl" };
  const rawUrl = values.livekitUrl.trim();
  // Check the original spelling before URL parsing can erase a path, empty
  // query, backslash, or user-info delimiter.
  if (rawUrl.length > 2048 || !/^wss?:\/\/[^\s\/\\?#@]+\/?$/i.test(rawUrl)) {
    return { error: "Use a wss:// LiveKit server URL with no path, credentials, query, or fragment.", field: "livekitUrl" };
  }
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return { error: "Enter a valid LiveKit server URL.", field: "livekitUrl" };
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (!url.hostname || url.username || url.password || (url.protocol !== "wss:" && !(url.protocol === "ws:" && loopback))) {
    return { error: "Use wss:// for LiveKit, or ws:// only for a local development server.", field: "livekitUrl" };
  }
  candidate.livekitUrl = url.origin;
  return { candidate };
}

function settingsFor(credentials: ConnectionInput | null, error?: string): ConnectionSettings {
  return {
    configured: Boolean(credentials),
    livekitUrl: credentials?.livekitUrl ?? "",
    hasGoogleApiKey: Boolean(credentials?.googleApiKey),
    hasLivekitApiKey: Boolean(credentials?.livekitApiKey),
    hasLivekitApiSecret: Boolean(credentials?.livekitApiSecret),
    ...(error ? { error } : {}),
  };
}

function safeValidationError(result: ConnectionValidationError, candidate: ConnectionInput): ConnectionValidationError {
  let error = typeof result.error === "string" && result.error.trim() ? result.error : validationFailed;
  for (const field of fields) error = error.replaceAll(candidate[field], "[redacted]");
  error = error.replace(/[\x00-\x1f\x7f]/g, " ").slice(0, 512);
  return { error, ...(result.field && fields.includes(result.field) ? { field: result.field } : {}) };
}

async function writeEncrypted(path: string, ciphertext: Buffer): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${randomUUID()}.tmp`;
  let file: Awaited<ReturnType<typeof open>> | undefined;
  try {
    file = await open(temporary, "wx", 0o600);
    await file.writeFile(JSON.stringify({ version: 1, ciphertext: ciphertext.toString("base64") }), "utf8");
    await file.sync();
    await file.close();
    file = undefined;
    await rename(temporary, path);
  } finally {
    await file?.close().catch(() => {});
    await rm(temporary, { force: true }).catch(() => {});
  }
}

export function createConnectionStore(options: ConnectionStoreOptions): ConnectionStore {
  let credentials: ConnectionInput | null = null;
  let loadError: string | undefined;
  let initialization: Promise<void> | undefined;
  let saves: Promise<unknown> = Promise.resolve();
  const validate = options.validate ?? validateConnection;

  function encryptionAvailable(): boolean {
    try {
      return options.encryption.isEncryptionAvailable() && options.encryption.getSelectedStorageBackend?.() !== "basic_text";
    } catch { return false; }
  }

  async function load(): Promise<void> {
    loadError = undefined;
    if (!encryptionAvailable()) { loadError = storageUnavailable; return; }
    try {
      const disk = await readFile(options.path, "utf8");
      if (disk.length > 65_536) throw new Error("Invalid encrypted settings.");
      const envelope = JSON.parse(disk);
      const ciphertext = envelope?.ciphertext;
      if (envelope?.version !== 1 || typeof ciphertext !== "string" || !ciphertext || ciphertext.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(ciphertext)) {
        throw new Error("Invalid encrypted settings.");
      }
      const encrypted = Buffer.from(ciphertext, "base64");
      if (encrypted.toString("base64") !== ciphertext) throw new Error("Invalid encrypted settings.");
      const normalized = normalizeInput(JSON.parse(options.encryption.decryptString(encrypted)));
      if ("error" in normalized) throw new Error("Incomplete encrypted settings.");
      credentials = normalized.candidate;
    } catch (error) {
      if (!(error && typeof error === "object" && "code" in error && error.code === "ENOENT")) loadError = unreadableSettings;
    }
  }

  async function ensureLoaded(): Promise<void> {
    if (credentials) return;
    initialization ??= load();
    await initialization;
    // Share each pending load, but allow a later UI retry to recover from a
    // temporarily locked key store or filesystem. Successful loads stay cached.
    if (!credentials && loadError) initialization = undefined;
  }

  async function save(input: unknown): Promise<ConnectionSaveResult> {
    await ensureLoaded();
    if (!encryptionAvailable()) return { error: storageUnavailable };
    let candidate: ConnectionInput;
    try {
      const normalized = normalizeInput(input, credentials);
      if ("error" in normalized) return normalized;
      candidate = normalized.candidate;
      const result = await validate(Object.freeze({ ...candidate }));
      if (result) return safeValidationError(result, candidate);
    } catch {
      return { error: validationFailed };
    }
    if (!encryptionAvailable()) return { error: storageUnavailable };
    try {
      const ciphertext = options.encryption.encryptString(JSON.stringify(candidate));
      if (!Buffer.isBuffer(ciphertext) || !ciphertext.length) throw new Error("Encryption failed.");
      await writeEncrypted(options.path, ciphertext);
    } catch {
      return { error: "Unable to securely save connection settings. Check your operating system key store and file permissions." };
    }
    // Only expose a candidate to supervised processes after durable replacement.
    credentials = candidate;
    loadError = undefined;
    return { settings: settingsFor(credentials) };
  }

  return {
    async get() {
      await ensureLoaded();
      return encryptionAvailable() ? settingsFor(credentials, loadError) : settingsFor(null, storageUnavailable);
    },
    save(input) {
      const operation = saves.then(() => save(input));
      saves = operation.catch(() => {});
      return operation;
    },
    async childEnv() {
      await ensureLoaded();
      if (!encryptionAvailable() || !credentials) return { ...emptyEnvironment };
      return {
        GOOGLE_API_KEY: credentials.googleApiKey,
        LIVEKIT_URL: credentials.livekitUrl,
        LIVEKIT_API_KEY: credentials.livekitApiKey,
        LIVEKIT_API_SECRET: credentials.livekitApiSecret,
      };
    },
  };
}

async function readSuccessObject(response: Response, signal: AbortSignal): Promise<Record<string, unknown>> {
  if (!response.body || signal.aborted) throw new Error("Missing authentication response.");
  const reader = response.body.getReader();
  const cancel = () => { reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  const decoder = new TextDecoder();
  const chunks: string[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 1_048_576) throw new Error("Authentication response is too large.");
      chunks.push(decoder.decode(value, { stream: true }));
    }
    chunks.push(decoder.decode());
    const object = JSON.parse(chunks.join(""));
    if (!object || typeof object !== "object" || Array.isArray(object)) throw new Error("Invalid authentication response.");
    return object;
  } finally {
    signal.removeEventListener("abort", cancel);
    cancel();
    reader.releaseLock();
  }
}

async function authenticate(request: typeof fetch, url: string, init: RequestInit, provider: "Google" | "LiveKit"): Promise<number | ConnectionValidationError> {
  const controller = new AbortController();
  let timedOut = false;
  let invalidResponse = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
      reject(new Error("Authentication timed out."));
    }, 10_000);
  });
  try {
    const responseWork = async () => {
      const response = await request(url, { ...init, redirect: "error", signal: controller.signal });
      if (provider === "LiveKit" && response.status >= 200 && response.status < 300) {
        try {
          const payload = await readSuccessObject(response, controller.signal);
          if (Object.keys(payload).length !== 0 && !Array.isArray(payload.rooms)) throw new Error("Invalid RoomService response.");
        } catch {
          invalidResponse = !timedOut;
          throw new Error("Invalid authentication response.");
        }
      } else {
        // Provider failures may echo credentials. Discard them without parsing
        // or exposing their bodies to the renderer.
        response.body?.cancel().catch(() => {});
      }
      return response.status;
    };
    return await Promise.race([responseWork(), timeout]);
  } catch {
    return {
      error: timedOut ? `${provider} authentication timed out. Check your network and try again.`
        : invalidResponse ? `${provider} returned an invalid authentication response.${provider === "LiveKit" ? " Check the LiveKit server URL." : " Try again later."}`
          : `Unable to reach ${provider}. Check your network${provider === "LiveKit" ? " and server URL" : ""}, then try again.`,
      field: provider === "Google" ? "googleApiKey" : "livekitUrl",
    };
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

export async function validateConnection(candidate: ConnectionInput, request: typeof fetch = fetch): Promise<ConnectionValidationError | null> {
  const normalized = normalizeInput(candidate);
  if ("error" in normalized) return normalized;
  candidate = normalized.candidate;
  const google = await authenticate(request, "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100", {
    method: "GET",
    headers: { "x-goog-api-key": candidate.googleApiKey },
  }, "Google");
  if (typeof google !== "number") return google;
  if (google < 200 || google >= 300) {
    const error = google === 400 || google === 401 ? "Google rejected this API key. Enter a valid Google API key."
      : google === 403 ? "Google denied permission. Enable the Generative Language API for this key."
        : google === 429 ? "Google quota or billing limits prevented validation. Check your Google project and try again."
          : google >= 500 ? "Google is temporarily unavailable. Try again later."
            : "Google could not validate this API key. Check your Google project and try again.";
    return { error, field: "googleApiKey" };
  }

  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const claims = Buffer.from(JSON.stringify({ iss: candidate.livekitApiKey, nbf: now, exp: now + 60, video: { roomList: true } })).toString("base64url");
  const signature = createHmac("sha256", candidate.livekitApiSecret).update(`${header}.${claims}`).digest("base64url");
  const url = new URL(candidate.livekitUrl);
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  const livekit = await authenticate(request, `${url.origin}/twirp/livekit.RoomService/ListRooms`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${header}.${claims}.${signature}` },
    body: "{}",
  }, "LiveKit");
  if (typeof livekit !== "number") return livekit;
  if (livekit >= 200 && livekit < 300) return null;
  if (livekit === 401 || livekit === 403) return { error: "LiveKit rejected this API key and secret. Check that they belong to this server.", field: "livekitApiSecret" };
  return {
    error: livekit === 429 ? "LiveKit quota limits prevented validation. Check your project and try again."
      : livekit >= 500 ? "LiveKit is temporarily unavailable. Try again later."
        : "The server did not accept a LiveKit authentication request. Check the LiveKit URL.",
    field: "livekitUrl",
  };
}
