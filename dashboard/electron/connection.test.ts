// @vitest-environment node
import { createCipheriv, createDecipheriv, createHmac, randomBytes } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, rename, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import type { ConnectionInput } from "../shared/types";
import { createConnectionStore, validateConnection } from "./connection";

const credentials: ConnectionInput = {
  googleApiKey: "google-private-test-key",
  livekitUrl: "wss://my-project.livekit.cloud",
  livekitApiKey: "livekit-private-test-key",
  livekitApiSecret: "livekit-private-test-secret",
};
const configured = {
  configured: true,
  livekitUrl: "wss://my-project.livekit.cloud",
  hasGoogleApiKey: true,
  hasLivekitApiKey: true,
  hasLivekitApiSecret: true,
};
const emptyEnv = {
  GOOGLE_API_KEY: "",
  LIVEKIT_URL: "",
  LIVEKIT_API_KEY: "",
  LIVEKIT_API_SECRET: "",
};
const directories: string[] = [];

// A real authenticated cipher stands in for Electron's platform-bound cipher.
// Disk writes, reads, serialization, and validation all run in the real store.
function cipher() {
  const key = randomBytes(32);
  return {
    isEncryptionAvailable: () => true,
    encryptString(value: string) {
      const nonce = randomBytes(12);
      const encoder = createCipheriv("aes-256-gcm", key, nonce);
      const encrypted = Buffer.concat([encoder.update(value, "utf8"), encoder.final()]);
      return Buffer.concat([nonce, encoder.getAuthTag(), encrypted]);
    },
    decryptString(value: Buffer) {
      const decoder = createDecipheriv("aes-256-gcm", key, value.subarray(0, 12));
      decoder.setAuthTag(value.subarray(12, 28));
      return Buffer.concat([decoder.update(value.subarray(28)), decoder.final()]).toString("utf8");
    },
  };
}

async function fixture(validate = async (_input: ConnectionInput) => null as { error: string; field?: keyof ConnectionInput } | null) {
  const directory = await mkdtemp(join(tmpdir(), "ira-connection-"));
  directories.push(directory);
  const path = join(directory, "connection.json");
  const encryption = cipher();
  return { directory, path, encryption, store: createConnectionStore({ path, encryption, validate }) };
}

afterEach(async () => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("secure connection store", () => {
  it("overrides all four inherited environment values for a fresh user", async () => {
    const { store } = await fixture();
    expect(await store.childEnv()).toEqual(emptyEnv);
    expect(await store.get()).toEqual({
      configured: false,
      livekitUrl: "",
      hasGoogleApiKey: false,
      hasLivekitApiKey: false,
      hasLivekitApiSecret: false,
    });
  });

  it("stores encrypted credentials and restores them in a new instance", async () => {
    const { path, encryption, store, directory } = await fixture();
    expect(await store.save(credentials)).toEqual({ settings: configured });
    const disk = await readFile(path, "utf8");
    for (const value of Object.values(credentials)) expect(disk).not.toContain(value);
    expect(JSON.parse(disk)).toEqual({ version: 1, ciphertext: expect.any(String) });
    expect(await readdir(directory)).toEqual(["connection.json"]);
    if (process.platform !== "win32") expect((await stat(path)).mode & 0o777).toBe(0o600);

    const reopened = createConnectionStore({ path, encryption, validate: async () => null });
    expect(await reopened.get()).toEqual(configured);
    expect(await reopened.childEnv()).toEqual({
      GOOGLE_API_KEY: "google-private-test-key",
      LIVEKIT_URL: "wss://my-project.livekit.cloud",
      LIVEKIT_API_KEY: "livekit-private-test-key",
      LIVEKIT_API_SECRET: "livekit-private-test-secret",
    });
    expect(JSON.stringify(await reopened.get())).not.toContain("private-test");
  });

  it("retains saved secrets when blank key fields update the server URL", async () => {
    const candidates: ConnectionInput[] = [];
    const { store } = await fixture(async (input) => { candidates.push(input); return null; });
    await store.save(credentials);
    expect(await store.save({ googleApiKey: "", livekitUrl: " WSS://OTHER.example/ ", livekitApiKey: "", livekitApiSecret: "" })).toEqual({
      settings: { ...configured, livekitUrl: "wss://other.example" },
    });
    expect(candidates[1]).toEqual({ ...credentials, livekitUrl: "wss://other.example" });
    expect(await store.childEnv()).toEqual({
      GOOGLE_API_KEY: "google-private-test-key",
      LIVEKIT_URL: "wss://other.example",
      LIVEKIT_API_KEY: "livekit-private-test-key",
      LIVEKIT_API_SECRET: "livekit-private-test-secret",
    });
  });

  it.each([
    [null, undefined],
    [{ ...credentials, googleApiKey: 123 }, "googleApiKey"],
    [{ ...credentials, googleApiKey: "" }, "googleApiKey"],
    [{ ...credentials, livekitApiKey: "" }, "livekitApiKey"],
    [{ ...credentials, livekitApiSecret: "" }, "livekitApiSecret"],
    [{ ...credentials, googleApiKey: "key with whitespace" }, "googleApiKey"],
    [{ ...credentials, livekitApiKey: "key\u0000injection" }, "livekitApiKey"],
    [{ ...credentials, livekitApiSecret: "key\nheader" }, "livekitApiSecret"],
    [{ ...credentials, googleApiKey: "x".repeat(513) }, "googleApiKey"],
    [{ ...credentials, livekitApiSecret: "x".repeat(2049) }, "livekitApiSecret"],
  ])("rejects incomplete or unsafe input before validation: %j", async (input, field) => {
    let validations = 0;
    const { store, directory } = await fixture(async () => { validations++; return null; });
    const result = await store.save(input);
    expect(result).toHaveProperty("error");
    if (field) expect(result).toHaveProperty("field", field);
    expect(validations).toBe(0);
    expect(await readdir(directory)).toEqual([]);
    expect(await store.childEnv()).toEqual(emptyEnv);
  });

  it.each([
    "https://my-project.livekit.cloud", "ws://remote.example", "wss://user:password@host.example",
    "wss://@host.example", "wss://host.example/path", "wss://host.example/../", "wss://host.example//",
    "wss://host.example?key=secret", "wss://host.example?", "wss://host.example#", "wss://host.example/#room",
    "wss://host.example\\path", "wss://", "",
  ])("rejects a server URL that cannot safely receive credentials: %s", async (livekitUrl) => {
    const { store } = await fixture();
    expect(await store.save({ ...credentials, livekitUrl })).toMatchObject({ error: expect.any(String), field: "livekitUrl" });
  });

  it.each(["ws://localhost:7880/", "ws://127.0.0.1:7880/", "ws://[::1]:7880/"])("allows a local development server: %s", async (livekitUrl) => {
    const { store } = await fixture();
    expect(await store.save({ ...credentials, livekitUrl })).toEqual({ settings: { ...configured, livekitUrl: livekitUrl.slice(0, -1) } });
  });

  it("keeps the previous file and environment when provider validation fails", async () => {
    const { store, path } = await fixture(async (input) => input.googleApiKey === "rejected-key" ? { error: "Google key rejected.", field: "googleApiKey" } : null);
    await store.save(credentials);
    const disk = await readFile(path, "utf8");
    expect(await store.save({ ...credentials, googleApiKey: "rejected-key" })).toMatchObject({ error: expect.any(String), field: "googleApiKey" });
    expect(await readFile(path, "utf8")).toBe(disk);
    expect(await store.get()).toEqual(configured);
    expect((await store.childEnv()).GOOGLE_API_KEY).toBe("google-private-test-key");
  });

  it("uses provider authentication by default and leaves rejected credentials unsaved", async () => {
    const { path, encryption, directory } = await fixture();
    vi.stubGlobal("fetch", async () => new Response("rejected", { status: 401 }));
    const store = createConnectionStore({ path, encryption });
    expect(await store.save(credentials)).toMatchObject({ field: "googleApiKey", error: expect.any(String) });
    expect(await readdir(directory)).toEqual([]);
    expect(await store.childEnv()).toEqual(emptyEnv);
  });

  it("redacts secrets returned in a validator error", async () => {
    const { store } = await fixture(async () => ({ error: `Rejected ${credentials.googleApiKey} and ${credentials.livekitApiSecret}`, field: "googleApiKey" }));
    const result = await store.save(credentials);
    expect(result).toMatchObject({ error: expect.any(String), field: "googleApiKey" });
    for (const value of Object.values(credentials)) expect(JSON.stringify(result)).not.toContain(value);
  });

  it("keeps old credentials when a filesystem obstruction prevents replacement", async () => {
    const { store, path, directory } = await fixture();
    await store.save(credentials);
    const backup = join(directory, "previous.json");
    await rename(path, backup);
    await mkdir(path);
    const disk = await readFile(backup, "utf8");
    expect(await store.save({ ...credentials, googleApiKey: "replacement-key" })).toHaveProperty("error");
    expect(await store.get()).toEqual(configured);
    expect((await store.childEnv()).GOOGLE_API_KEY).toBe("google-private-test-key");
    expect(await readFile(backup, "utf8")).toBe(disk);
    expect((await readdir(directory)).sort()).toEqual(["connection.json", "previous.json"]);
  });

  it("never falls back to plaintext when encryption is unavailable", async () => {
    const { path, encryption, directory } = await fixture();
    const unavailable = createConnectionStore({ path, encryption: { ...encryption, isEncryptionAvailable: () => false }, validate: async () => null });
    expect(await unavailable.save(credentials)).toHaveProperty("error");
    expect(await unavailable.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await unavailable.childEnv()).toEqual(emptyEnv);
    expect(await readdir(directory)).toEqual([]);
  });

  it("rejects Electron's basic_text backend even when encryption reports available", async () => {
    const { path, encryption, directory } = await fixture();
    const store = createConnectionStore({ path, encryption: { ...encryption, getSelectedStorageBackend: () => "basic_text" }, validate: async () => null });
    expect(await store.save(credentials)).toHaveProperty("error");
    expect(await store.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await store.childEnv()).toEqual(emptyEnv);
    expect(await readdir(directory)).toEqual([]);
  });

  it("withholds cached credentials when the operating system key store becomes unavailable", async () => {
    const { path, encryption } = await fixture();
    let available = true;
    const store = createConnectionStore({ path, encryption: { ...encryption, isEncryptionAvailable: () => available }, validate: async () => null });
    await store.save(credentials);
    available = false;
    expect(await store.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await store.childEnv()).toEqual(emptyEnv);
  });

  it("retries a saved encrypted file after a transient decrypt failure", async () => {
    const { store, path, encryption } = await fixture();
    await store.save(credentials);
    let locked = true;
    const reopened = createConnectionStore({
      path,
      encryption: { ...encryption, decryptString: (value) => { if (locked) throw new Error("Key store is temporarily locked."); return encryption.decryptString(value); } },
      validate: async () => null,
    });
    expect(await reopened.get()).toMatchObject({ configured: false, error: expect.any(String) });
    locked = false;
    expect(await reopened.get()).toEqual(configured);
    expect((await reopened.childEnv()).GOOGLE_API_KEY).toBe(credentials.googleApiKey);
  });

  it("retries a saved encrypted file when the operating system key store recovers", async () => {
    const { store, path, encryption } = await fixture();
    await store.save(credentials);
    let available = false;
    const reopened = createConnectionStore({ path, encryption: { ...encryption, isEncryptionAvailable: () => available }, validate: async () => null });
    expect(await reopened.get()).toMatchObject({ configured: false, error: expect.any(String) });
    available = true;
    expect(await reopened.get()).toEqual(configured);
    expect((await reopened.childEnv()).LIVEKIT_API_SECRET).toBe(credentials.livekitApiSecret);
  });

  it("does not write when secure storage becomes unavailable during provider validation", async () => {
    const { path, encryption, directory } = await fixture();
    let available = true;
    const store = createConnectionStore({
      path,
      encryption: { ...encryption, isEncryptionAvailable: () => available },
      validate: async () => { available = false; return null; },
    });
    expect(await store.save(credentials)).toHaveProperty("error");
    expect(await readdir(directory)).toEqual([]);
    expect(await store.childEnv()).toEqual(emptyEnv);
  });

  it.each(["not json", '{"version":1,"ciphertext":"not-base64!"}', '{"version":2,"ciphertext":"YWJj"}', JSON.stringify(credentials)])("blocks startup credentials from a corrupt or plaintext file: %s", async (disk) => {
    const { path, encryption } = await fixture();
    await writeFile(path, disk);
    const reopened = createConnectionStore({ path, encryption, validate: async () => null });
    expect(await reopened.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await reopened.childEnv()).toEqual(emptyEnv);
  });

  it("rejects an encrypted payload that contains only some credentials", async () => {
    const { path, encryption } = await fixture();
    await writeFile(path, JSON.stringify({ version: 1, ciphertext: encryption.encryptString(JSON.stringify({ googleApiKey: credentials.googleApiKey })).toString("base64") }));
    const reopened = createConnectionStore({ path, encryption, validate: async () => null });
    expect(await reopened.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await reopened.childEnv()).toEqual(emptyEnv);
  });

  it("rejects unsafe server URLs inside an otherwise encrypted file", async () => {
    const { path, encryption } = await fixture();
    const payload = { ...credentials, livekitUrl: "wss://user:password@host.example" };
    await writeFile(path, JSON.stringify({ version: 1, ciphertext: encryption.encryptString(JSON.stringify(payload)).toString("base64") }));
    const reopened = createConnectionStore({ path, encryption, validate: async () => null });
    expect(await reopened.get()).toMatchObject({ configured: false, error: expect.any(String) });
    expect(await reopened.childEnv()).toEqual(emptyEnv);
  });

  it("serializes saves so queued blank fields retain the newest credentials", async () => {
    let entered!: () => void;
    let release!: () => void;
    const started = new Promise<void>((resolve) => { entered = resolve; });
    const blocked = new Promise<void>((resolve) => { release = resolve; });
    const candidates: ConnectionInput[] = [];
    const { store, path, encryption } = await fixture(async (input) => {
      candidates.push(input);
      if (candidates.length === 1) { entered(); await blocked; }
      return null;
    });
    const first = store.save(credentials);
    await started;
    const second = store.save({ googleApiKey: "", livekitUrl: "wss://newest.example", livekitApiKey: "", livekitApiSecret: "" });
    expect(candidates).toHaveLength(1);
    expect(await store.childEnv()).toEqual(emptyEnv);
    expect(await store.get()).toMatchObject({ configured: false });
    release();
    expect(await first).toHaveProperty("settings");
    expect(await second).toHaveProperty("settings");
    expect(candidates[1]).toEqual({ ...credentials, livekitUrl: "wss://newest.example" });
    const reopened = createConnectionStore({ path, encryption, validate: async () => null });
    expect(await reopened.get()).toEqual({ ...configured, livekitUrl: "wss://newest.example" });
  });

  it("does not expose secrets from a thrown validator or cipher", async () => {
    const { store, path, encryption } = await fixture(async () => { throw new Error(credentials.googleApiKey); });
    const brokenCipher = createConnectionStore({ path, encryption: { ...encryption, encryptString: () => { throw new Error(credentials.livekitApiSecret); } }, validate: async () => null });
    const results = [await store.save(credentials), await brokenCipher.save(credentials)];
    for (const result of results) {
      expect(result).toHaveProperty("error");
      for (const value of Object.values(credentials)) expect(JSON.stringify(result)).not.toContain(value);
    }
  });

  it("continues saving after an earlier queued validator throws", async () => {
    const { store } = await fixture(async (input) => {
      if (input.googleApiKey === "rejected-key") throw new Error(input.googleApiKey);
      return null;
    });
    const rejected = store.save({ ...credentials, googleApiKey: "rejected-key" });
    const accepted = store.save(credentials);
    expect(await rejected).toHaveProperty("error");
    expect(await accepted).toEqual({ settings: configured });
    expect((await store.childEnv()).GOOGLE_API_KEY).toBe("google-private-test-key");
  });
});

describe("provider authentication", () => {
  it("authenticates both providers with safe headers and a verifiable short-lived LiveKit JWT", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const request: typeof fetch = async (url, init) => {
      calls.push({ url: String(url), init: init! });
      return new Response("{}", { status: 200 });
    };
    const before = Math.floor(Date.now() / 1000);
    expect(await validateConnection({ ...credentials, livekitUrl: "wss://my-project.livekit.cloud/" }, request)).toBeNull();
    expect(calls).toHaveLength(2);
    const google = calls.find((call) => call.url.startsWith("https://generativelanguage.googleapis.com/"))!;
    expect(google.url).toBe("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100");
    expect(google.init.method).toBe("GET");
    expect(new Headers(google.init.headers).get("x-goog-api-key")).toBe("google-private-test-key");
    const livekit = calls.find((call) => call.url.includes("/twirp/"))!;
    expect(livekit.url).toBe("https://my-project.livekit.cloud/twirp/livekit.RoomService/ListRooms");
    expect(livekit.init.method).toBe("POST");
    expect(livekit.init.body).toBe("{}");
    expect(new Headers(livekit.init.headers).get("Content-Type")).toBe("application/json");
    const token = new Headers(livekit.init.headers).get("Authorization")!.replace(/^Bearer /, "");
    const [header, claims, signature] = token.split(".");
    expect(JSON.parse(Buffer.from(header, "base64url").toString())).toEqual({ alg: "HS256", typ: "JWT" });
    const payload = JSON.parse(Buffer.from(claims, "base64url").toString());
    expect(payload.iss).toBe("livekit-private-test-key");
    expect(payload.nbf).toBeGreaterThanOrEqual(before);
    expect(payload.nbf).toBeLessThanOrEqual(Math.floor(Date.now() / 1000));
    expect(payload.video).toEqual({ roomList: true });
    expect(payload.exp).toBeGreaterThanOrEqual(before + 60);
    expect(payload.exp).toBeLessThanOrEqual(Math.floor(Date.now() / 1000) + 60);
    expect(signature).toBe(createHmac("sha256", "livekit-private-test-secret").update(`${header}.${claims}`).digest("base64url"));
    for (const call of calls) {
      expect(call.init.redirect).toBe("error");
      expect(call.init.signal).toBeInstanceOf(AbortSignal);
      expect(call.url).not.toContain("private-test");
    }
  });

  it("uses HTTP only for an explicitly allowed loopback LiveKit server", async () => {
    const urls: string[] = [];
    const request: typeof fetch = async (url) => { urls.push(String(url)); return new Response("{}", { status: 200 }); };
    expect(await validateConnection({ ...credentials, livekitUrl: "ws://127.0.0.1:7880" }, request)).toBeNull();
    expect(urls).toContain("http://127.0.0.1:7880/twirp/livekit.RoomService/ListRooms");
  });

  it.each([
    [400, /rejected|invalid/i], [401, /rejected|invalid/i], [403, /permission/i], [429, /quota|billing/i], [503, /unavailable/i],
  ])("reports Google status %i without returning provider bodies", async (status, message) => {
    const request: typeof fetch = async (url) => new Response(JSON.stringify({ error: { message: credentials.googleApiKey } }), { status: String(url).includes("googleapis") ? status : 200 });
    const result = await validateConnection(credentials, request);
    expect(result).toMatchObject({ field: "googleApiKey", error: expect.stringMatching(message) });
    expect(JSON.stringify(result)).not.toContain(credentials.googleApiKey);
  });

  it.each([401, 403])("rejects LiveKit status %i as a credential error", async (status) => {
    const request: typeof fetch = async (url) => new Response(credentials.livekitApiSecret, { status: String(url).includes("/twirp/") ? status : 200 });
    const result = await validateConnection(credentials, request);
    expect(result).toMatchObject({ field: "livekitApiSecret", error: expect.stringMatching(/key.*secret/i) });
    expect(JSON.stringify(result)).not.toContain(credentials.livekitApiSecret);
  });

  it("reports a LiveKit network failure against the server URL", async () => {
    const request: typeof fetch = async (url) => {
      if (String(url).includes("/twirp/")) throw new Error(credentials.livekitApiSecret);
      return new Response("{}", { status: 200 });
    };
    expect(await validateConnection(credentials, request)).toMatchObject({ field: "livekitUrl", error: expect.stringMatching(/network|reach/i) });
  });

  it("reports a Google network failure without the underlying error", async () => {
    const request: typeof fetch = async (url) => {
      if (String(url).includes("googleapis")) throw new Error(credentials.googleApiKey);
      return new Response("{}", { status: 200 });
    };
    const result = await validateConnection(credentials, request);
    expect(result).toMatchObject({ field: "googleApiKey", error: expect.stringMatching(/network|reach/i) });
    expect(JSON.stringify(result)).not.toContain(credentials.googleApiKey);
  });

  it("rejects unsafe input before making authentication requests", async () => {
    let requests = 0;
    const request: typeof fetch = async () => { requests++; return new Response("{}", { status: 200 }); };
    expect(await validateConnection({ ...credentials, livekitUrl: "wss://user:secret@host.example" }, request)).toMatchObject({ field: "livekitUrl", error: expect.any(String) });
    expect(requests).toBe(0);
  });

  it.each(["Google", "LiveKit"])("bounds a stalled %s authentication request and aborts it", async (provider) => {
    vi.useFakeTimers();
    let stalledSignal: AbortSignal | null = null;
    const request: typeof fetch = async (url, init) => {
      const isGoogle = String(url).includes("googleapis");
      if ((provider === "Google") === isGoogle) {
        stalledSignal = init!.signal!;
        return new Promise<Response>(() => {});
      }
      return new Response("{}", { status: 200 });
    };
    const result = expect(validateConnection(credentials, request)).resolves.toMatchObject({
      field: provider === "Google" ? "googleApiKey" : "livekitUrl",
      error: expect.stringMatching(/timed out|timeout/i),
    });
    // Attach the expectation before advancing the clock so rejected implementations
    // still fail within the test instead of becoming unhandled rejections.
    result.catch(() => {});
    await vi.advanceTimersByTimeAsync(15_000);
    await result;
    expect(stalledSignal!.aborted).toBe(true);
  });

  it.each([404, 429, 503])("reports LiveKit status %i against its URL without provider response data", async (status) => {
    const request: typeof fetch = async (url) => new Response(credentials.livekitApiSecret, { status: String(url).includes("/twirp/") ? status : 200 });
    const result = await validateConnection(credentials, request);
    expect(result).toMatchObject({ field: "livekitUrl", error: expect.any(String) });
    expect(JSON.stringify(result)).not.toContain(credentials.livekitApiSecret);
  });

  it.each(["<html>website fallback</html>", "not json", "null", "[]", '{"rooms":"wrong type"}', '{"ok":true}'])("rejects a non-RoomService success response: %s", async (body) => {
    const request: typeof fetch = async (url) => new Response(String(url).includes("/twirp/") ? body : "{}", { status: 200 });
    expect(await validateConnection(credentials, request)).toMatchObject({ field: "livekitUrl", error: expect.any(String) });
  });

  it("accepts a successful ListRooms response containing room metadata", async () => {
    const request: typeof fetch = async (url) => new Response(String(url).includes("/twirp/") ? '{"rooms":[{"sid":"RM_test","name":"test-room"}]}' : "{}", { status: 200 });
    expect(await validateConnection(credentials, request)).toBeNull();
  });

  it("rejects an oversized successful provider response without echoing it", async () => {
    const request: typeof fetch = async (url) => new Response(String(url).includes("/twirp/") ? JSON.stringify({ rooms: [], echoedSecret: credentials.livekitApiSecret, padding: "x".repeat(1_048_577) }) : "{}", { status: 200 });
    const result = await validateConnection(credentials, request);
    expect(result).toMatchObject({ field: "livekitUrl", error: expect.any(String) });
    expect(JSON.stringify(result)).not.toContain(credentials.livekitApiSecret);
  });

  it("bounds a stalled LiveKit response body and releases its stream", async () => {
    vi.useFakeTimers();
    let cancelled = false;
    const request: typeof fetch = async (url) => new Response(String(url).includes("/twirp/") ? new ReadableStream({ cancel() { cancelled = true; } }) : "{}", { status: 200 });
    const result = expect(validateConnection(credentials, request)).resolves.toMatchObject({ field: "livekitUrl", error: expect.stringMatching(/timed out|timeout/i) });
    result.catch(() => {});
    await vi.advanceTimersByTimeAsync(15_000);
    await result;
    expect(cancelled).toBe(true);
  });
});
