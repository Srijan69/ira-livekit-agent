// @vitest-environment node
import { join } from "node:path";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";

import { afterEach, describe, expect, it } from "vitest";

import { ManagedProcess, type ProcessStatus } from "./supervisor";

/**
 * The supervisor runs real child processes here -- Node itself, executing tiny
 * scripts -- because the bugs worth catching are about real process
 * lifecycles: exit events arriving late, restarts racing them, and trees that
 * outlive their parent. A fake `spawn` would test none of that.
 */

const dir = mkdtempSync(join(tmpdir(), "ira-supervisor-"));

function script(name: string, body: string): string {
  const path = join(dir, name);
  writeFileSync(path, body);
  return path;
}

const EXIT_AT_ONCE = script("exit.cjs", "process.exit(3);");
const PRINT_READY = script(
  "ready.cjs",
  "console.log('booting'); setTimeout(() => console.log('registered worker'), 50); setInterval(() => {}, 1000);",
);
const LIVE_FOREVER = script("forever.cjs", "setInterval(() => {}, 1000);");

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(
  predicate: () => boolean,
  timeoutMs = 4_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("timed out waiting for condition");
    await sleep(20);
  }
}

const running: ManagedProcess[] = [];

function managed(
  path: string,
  extra: Partial<ConstructorParameters<typeof ManagedProcess>[3]> = {},
): { proc: ManagedProcess; statuses: ProcessStatus[] } {
  const statuses: ProcessStatus[] = [];
  const proc = new ManagedProcess("test", process.execPath, [path], {
    cwd: dir,
    backoffMs: [20, 40],
    onStatus: (status) => statuses.push(status),
    ...extra,
  });
  running.push(proc);
  return { proc, statuses };
}

afterEach(() => {
  for (const proc of running.splice(0)) proc.stop();
});

describe("ManagedProcess", () => {
  it("gives up after its restart budget instead of looping forever", async () => {
    // A process failing for a configuration reason fails identically every
    // time. Endless retries would hide that behind a spinner.
    const { proc } = managed(EXIT_AT_ONCE);
    proc.start();

    await waitFor(() => proc.status().state === "failed");

    expect(proc.status().restarts).toBe(2);
    expect(proc.status().reason).toMatch(/gave up/);
  });

  it("keeps the last output for the failure panel", async () => {
    const { proc } = managed(EXIT_AT_ONCE);
    proc.start();
    await waitFor(() => proc.status().state === "failed");

    expect(proc.recentLogs().some((line) => line.includes("exited code=3"))).toBe(
      true,
    );
  });

  it("reports ready when the ready pattern appears in its output", async () => {
    const { proc, statuses } = managed(PRINT_READY, {
      readyPattern: /registered worker/,
    });
    proc.start();

    await waitFor(() => proc.status().state === "ready");
    // Running first, then ready -- "spawned" and "able to take a job" are
    // different facts, and the dashboard needs the second one.
    const states = statuses.map((status) => status.state);
    expect(states.indexOf("running")).toBeLessThan(states.indexOf("ready"));
  });

  it("stays running, not ready, without a ready pattern match", async () => {
    const { proc } = managed(LIVE_FOREVER, { readyPattern: /never printed/ });
    proc.start();
    await waitFor(() => proc.status().state === "running");
    await sleep(150);
    expect(proc.status().state).toBe("running");
  });

  it("does not spawn duplicates when restarted", async () => {
    // The bug this guards: the old child's exit event arrived after restart()
    // had already spawned its replacement, nulled the reference to the *new*
    // child, and scheduled a further restart -- two IRAs answering at once.
    const spawned: number[] = [];
    const { proc } = managed(LIVE_FOREVER, {
      onSpawn: (pid) => spawned.push(pid),
    });
    proc.start();
    await waitFor(() => proc.status().state === "running");

    proc.restart();
    await waitFor(() => spawned.length >= 2);
    // Long enough for a stray exit handler to have scheduled a third spawn.
    await sleep(300);

    expect(spawned).toHaveLength(2);
    expect(proc.status().state).toBe("running");
    expect(proc.status().restarts).toBe(0);
  });

  it("stop() kills the child and does not restart it", async () => {
    const { proc } = managed(LIVE_FOREVER);
    proc.start();
    await waitFor(() => proc.status().state === "running");
    const pid = proc.pid;
    expect(pid).toBeTypeOf("number");

    proc.stop();
    await sleep(400);

    expect(proc.status().state).toBe("stopped");
    expect(isAlive(pid!)).toBe(false);
  });

  it("passes UTF-8 settings to Python children", () => {
    // Piped stdout makes Python fall back to cp1252 on Windows, and the
    // LiveKit SDK then crashes printing its own startup emoji. Every child
    // gets these regardless of what the caller passes.
    const env = ManagedProcess.childEnv({ EXTRA: "1" });
    expect(env.PYTHONUTF8).toBe("1");
    expect(env.PYTHONIOENCODING).toBe("utf-8");
    expect(env.PYTHONUNBUFFERED).toBe("1");
    expect(env.EXTRA).toBe("1");
  });
});

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
