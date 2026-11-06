// @vitest-environment node
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, delimiter, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import type { ConnectionInput } from "../shared/types";
import { createConnectionStore } from "./connection";
import { ManagedProcess } from "./supervisor";

const repository = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const source = join(repository, "src");
const directories: string[] = [];
const processes: ManagedProcess[] = [];
const saved: ConnectionInput = {
  googleApiKey: "runtime-user-google-key",
  livekitUrl: "wss://runtime-user.livekit.cloud",
  livekitApiKey: "runtime-user-livekit-key",
  livekitApiSecret: "runtime-user-livekit-secret-for-offline-checks",
};
const uvArguments = ["run", "--project", repository, "--no-sync", "--offline", "--no-env-file", "python"];

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

// Probe each installed PATH candidate rather than assuming the first Windows
// uv launcher can run the project's Python. An override also supports CI.
function findUv(cwd: string): string {
  const executable = process.platform === "win32" ? "uv.exe" : "uv";
  const candidates = [
    process.env.IRA_TEST_UV,
    ...(process.env.PATH ?? "").split(delimiter).filter(Boolean).map((directory) => join(directory, executable)),
    executable,
  ];
  for (const candidate of new Set(candidates)) {
    if (!candidate || (candidate !== executable && !existsSync(candidate))) continue;
    const probe = spawnSync(candidate, [...uvArguments, "-c", "print('IRA_RUNTIME_UV_READY')"], {
      cwd,
      env: ManagedProcess.childEnv({ GOOGLE_API_KEY: "", LIVEKIT_URL: "", LIVEKIT_API_KEY: "", LIVEKIT_API_SECRET: "" }),
      encoding: "utf8",
      timeout: 5_000,
      windowsHide: true,
      shell: false,
    });
    if (probe.status === 0 && probe.stdout.trim() === "IRA_RUNTIME_UV_READY") return candidate;
  }
  throw new Error("No installed uv executable can run the project's Python for the offline runtime test.");
}

// The bridge's real load_dotenv call reads only the isolated cwd. We build
// real Google SDK model objects, but never create a session or open a socket.
// Only comparison booleans leave Python, including when a check fails.
const checkRuntime = String.raw`
import contextlib
import io
import json
import os
import sys
from unittest.mock import patch

result = {}
expected = json.loads(sys.argv[4])
configured = sys.argv[3] == "configured"

def no_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled in this runtime test")

try:
    if os.path.realpath(os.getcwd()) != os.path.realpath(sys.argv[2]):
        raise RuntimeError("Runtime test is not in its isolated directory")
    result["isolated_cwd"] = True
    os.environ.pop("IRA_CONNECTION_RUNTIME_DOTENV_PROBE", None)
    sys.path.insert(0, sys.argv[1])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with patch("socket.socket.connect", no_network), patch("socket.socket.connect_ex", no_network):
            from bridge import BridgeConfig
            from realtime_model import build_realtime_model

            result["fake_dotenv_read"] = os.getenv("IRA_CONNECTION_RUNTIME_DOTENV_PROBE") == "true"
            actual_env = {field: os.getenv(field) for field in expected}
            result["child_environment_matches_user"] = actual_env == expected
            config = BridgeConfig.from_env("offline-runtime-bridge-secret")
            bridge_values = {
                "GOOGLE_API_KEY": config.google_api_key,
                "LIVEKIT_URL": config.livekit_url,
                "LIVEKIT_API_KEY": config.api_key,
                "LIVEKIT_API_SECRET": config.api_secret,
            }
            result["bridge_matches_user"] = bridge_values == expected
            result["bridge_requires_credentials"] = (
                config.missing_credential() is None if configured
                else config.missing_credential() == "LIVEKIT_URL"
            )
            try:
                adapter = build_realtime_model(voice="Puck")
            except ValueError as error:
                result["google_model_boundary_verified"] = not configured and "GOOGLE_API_KEY" in str(error)
            else:
                result["google_model_boundary_verified"] = configured and len(adapter.models) == 2 and all(
                    model._opts.api_key == expected["GOOGLE_API_KEY"] and model._opts.vertexai is False
                    for model in adapter.models
                )
except BaseException:
    result["unexpected_exception"] = True

print("IRA_CONNECTION_RUNTIME=" + json.dumps(result, sort_keys=True), flush=True)
`;

async function runRuntime(cwd: string, env: NodeJS.ProcessEnv, configured: boolean): Promise<Record<string, boolean>> {
  const expected = {
    GOOGLE_API_KEY: configured ? saved.googleApiKey : "",
    LIVEKIT_URL: configured ? saved.livekitUrl : "",
    LIVEKIT_API_KEY: configured ? saved.livekitApiKey : "",
    LIVEKIT_API_SECRET: configured ? saved.livekitApiSecret : "",
  };
  const command = findUv(cwd);
  let captured: Record<string, boolean> | undefined;
  let resolveResult!: (result: Record<string, boolean>) => void;
  let rejectResult!: (error: Error) => void;
  const completed = new Promise<Record<string, boolean>>((resolveCheck, rejectCheck) => { resolveResult = resolveCheck; rejectResult = rejectCheck; });
  const managed = new ManagedProcess("offline-connection-runtime", command, [
    ...uvArguments, "-c", checkRuntime, source, cwd, configured ? "configured" : "unconfigured", JSON.stringify(expected),
  ], {
    cwd,
    env: {
      // These synthetic inherited values exercise the same override boundary
      // as an owner's shell environment without reading the real .env.local.
      GOOGLE_API_KEY: "fake-owner-parent-google-key",
      LIVEKIT_URL: "wss://fake-owner-parent.livekit.cloud",
      LIVEKIT_API_KEY: "fake-owner-parent-livekit-key",
      LIVEKIT_API_SECRET: "fake-owner-parent-livekit-secret",
      ...env,
      IRA_GEMINI_PRIMARY_MODEL: "",
      IRA_GEMINI_FALLBACK_MODEL: "",
      IRA_THINKING_LEVEL: "low",
    },
    backoffMs: [],
    onLog: (_name, line) => {
      if (line.startsWith("IRA_CONNECTION_RUNTIME=")) {
        try { captured = JSON.parse(line.slice("IRA_CONNECTION_RUNTIME=".length)); }
        catch { rejectResult(new Error("Python did not return valid runtime comparison markers.")); }
      }
    },
    onStatus: (status) => {
      if (status.state === "failed") {
        if (captured) resolveResult(captured);
        else rejectResult(new Error("The offline Python subprocess exited without runtime comparison markers."));
      }
    },
  });
  processes.push(managed);
  const timer = setTimeout(() => rejectResult(new Error("The offline Python runtime check timed out.")), 25_000);
  try {
    managed.start();
    return await completed;
  } finally {
    clearTimeout(timer);
    managed.stop();
  }
}

afterEach(async () => {
  for (const process of processes.splice(0)) process.stop();
  for (const directory of directories.splice(0)) {
    const target = await realpath(directory);
    const withinTemp = relative(await realpath(tmpdir()), target);
    if (!withinTemp || withinTemp.startsWith("..") || isAbsolute(withinTemp) || !basename(target).startsWith("ira-connection-runtime-")) {
      throw new Error("Refusing to remove a runtime fixture outside the verified temporary directory.");
    }
    await rm(target, { recursive: true, force: true });
  }
});

describe("saved connection credentials across the real Python runtime boundary", () => {
  it.each(["saved", "reopened", "unconfigured"] as const)("uses %s user settings through ManagedProcess, dotenv, BridgeConfig, and the Google SDK", async (mode) => {
    const cwd = await mkdtemp(join(tmpdir(), "ira-connection-runtime-"));
    directories.push(cwd);
    await writeFile(join(cwd, ".env.local"), [
      "GOOGLE_API_KEY=fake-owner-dotenv-google-key",
      "LIVEKIT_URL=wss://fake-owner-dotenv.livekit.cloud",
      "LIVEKIT_API_KEY=fake-owner-dotenv-livekit-key",
      "LIVEKIT_API_SECRET=fake-owner-dotenv-livekit-secret",
      "IRA_CONNECTION_RUNTIME_DOTENV_PROBE=true",
    ].join("\n"));
    const options = { path: join(cwd, "connection.json"), encryption: cipher(), validate: async () => null };
    let store = createConnectionStore(options);
    if (mode !== "unconfigured") expect(await store.save(saved)).toHaveProperty("settings");
    if (mode === "reopened") {
      store = createConnectionStore(options);
      expect(await store.get()).toMatchObject({ configured: true, livekitUrl: saved.livekitUrl });
    }
    expect(await runRuntime(cwd, await store.childEnv(), mode !== "unconfigured")).toEqual({
      isolated_cwd: true,
      fake_dotenv_read: true,
      child_environment_matches_user: true,
      bridge_matches_user: true,
      bridge_requires_credentials: true,
      google_model_boundary_verified: true,
    });
  }, 40_000);
});
