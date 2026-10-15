import { spawn, spawnSync, type ChildProcess } from "node:child_process";

import type { ProcessState, ProcessStatus } from "../shared/types";

export type { ProcessState, ProcessStatus };

/** Backoff between restarts. Length of the array is the attempt limit. */
const DEFAULT_BACKOFF_MS = [1_000, 2_000, 4_000];

/** Log lines kept per process, for the failure panel. */
const LOG_LIMIT = 200;

export type ManagedProcessOptions = {
  cwd: string;
  env?: NodeJS.ProcessEnv;
  /**
   * Output that means "able to do its job", as distinct from "spawned".
   *
   * For the agent that's the SDK's worker registration: a spawned worker that
   * hasn't registered yet can't receive a dispatch, and connecting before then
   * creates a room nobody will ever join.
   */
  readyPattern?: RegExp;
  backoffMs?: readonly number[];
  onStatus?: (status: ProcessStatus) => void;
  onLog?: (name: string, line: string) => void;
  /** Test hook: called with each spawned PID. */
  onSpawn?: (pid: number) => void;
};

/**
 * Supervises one child process.
 *
 * Electron owns the lifecycle of both Python processes, so double-clicking
 * IRA starts everything. The parts worth being careful about:
 *
 * - **Failure is visible.** Restarts are bounded; when they run out the
 *   process goes to "failed" with its last output attached, rather than the
 *   orb spinning on "connecting" with nothing to explain it.
 * - **No shell.** `shell: true` on Windows wraps the child in `cmd.exe`, so
 *   `kill()` kills cmd and orphans everything under it -- and it also breaks
 *   any executable path containing a space, such as `C:\Program Files`.
 *   `uv.exe` resolves without a shell, so none is used.
 * - **The whole tree dies.** `uv run` spawns Python, and the agent's dev mode
 *   spawns a worker that spawns job processes. Killing only the top one would
 *   leave a headless IRA holding the microphone.
 * - **Stale events are ignored.** A restarted process's old exit event
 *   arrives after its replacement has spawned; acting on it would null the
 *   new child and schedule a duplicate.
 */
export class ManagedProcess {
  private child: ChildProcess | null = null;
  private restarts = 0;
  private state: ProcessState = "stopped";
  private reason: string | undefined;
  private timer: NodeJS.Timeout | null = null;
  private stopping = false;
  private readonly logs: string[] = [];
  private readonly backoff: readonly number[];

  constructor(
    readonly name: string,
    private readonly command: string,
    private readonly args: string[],
    private readonly options: ManagedProcessOptions,
  ) {
    this.backoff = options.backoffMs ?? DEFAULT_BACKOFF_MS;
  }

  /**
   * Environment for every child, whatever the caller passes.
   *
   * Piped stdout makes Python fall back to cp1252 on Windows, and the LiveKit
   * SDK then crashes printing its own startup emoji (the README documents
   * this for redirected output). Unbuffered so log lines -- including the
   * ready line -- arrive when written, not when a 4KB buffer fills.
   */
  static childEnv(extra?: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
    return {
      ...process.env,
      ...extra,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONUNBUFFERED: "1",
    };
  }

  get pid(): number | undefined {
    return this.child?.pid;
  }

  status(): ProcessStatus {
    return {
      name: this.name,
      state: this.state,
      reason: this.reason,
      restarts: this.restarts,
    };
  }

  recentLogs(): string[] {
    return [...this.logs];
  }

  start(): void {
    if (this.child) return;
    this.stopping = false;
    this.setState("starting");

    const child = spawn(this.command, this.args, {
      cwd: this.options.cwd,
      env: ManagedProcess.childEnv(this.options.env),
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;

    // Every handler checks it still belongs to the current child. Events from
    // a replaced child are history, not instructions.
    const current = () => this.child === child;

    child.stdout?.on("data", (chunk) => current() && this.capture(String(chunk)));
    child.stderr?.on("data", (chunk) => current() && this.capture(String(chunk)));

    child.on("spawn", () => {
      if (!current()) return;
      if (child.pid !== undefined) this.options.onSpawn?.(child.pid);
      // Without a ready pattern, spawned is as ready as we can know.
      this.setState("running");
    });

    child.on("error", (error) => {
      if (!current()) return;
      this.child = null;
      this.capture(`[supervisor] spawn failed: ${error.message}`);
      this.fail(`could not start ${this.command}: ${error.message}`);
    });

    child.on("exit", (code, signal) => {
      if (!current()) return;
      this.child = null;
      if (this.stopping) {
        this.setState("stopped");
        return;
      }
      this.capture(`[supervisor] exited code=${code} signal=${signal}`);
      this.scheduleRestart(`${this.name} exited with code ${code}`);
    });
  }

  /** Manual restart from the UI. Clears the attempt budget. */
  restart(): void {
    this.stop();
    this.restarts = 0;
    this.reason = undefined;
    this.start();
  }

  stop(): void {
    this.stopping = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;

    const child = this.child;
    // Detach first, so the exit event this triggers is recognised as stale.
    this.child = null;
    if (child) killTree(child);

    this.setState("stopped");
  }

  private scheduleRestart(reason: string): void {
    const delay = this.backoff[this.restarts];
    if (delay === undefined) {
      // Out of attempts. A process failing for a configuration reason fails
      // identically forever, and hiding that behind endless retries is how you
      // end up debugging the wrong thing.
      this.fail(`${reason} (gave up after ${this.restarts} restarts)`);
      return;
    }

    this.restarts += 1;
    this.setState("restarting");
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.stopping) this.start();
    }, delay);
  }

  private fail(reason: string): void {
    this.reason = reason;
    this.setState("failed");
  }

  private setState(state: ProcessState): void {
    if (this.state === state) return;
    this.state = state;
    this.options.onStatus?.(this.status());
  }

  private capture(chunk: string): void {
    for (const line of chunk.split(/\r?\n/)) {
      if (!line.trim()) continue;
      this.logs.push(line);
      if (this.logs.length > LOG_LIMIT) this.logs.shift();
      this.options.onLog?.(this.name, line);

      if (
        this.state === "running" &&
        this.options.readyPattern?.test(line)
      ) {
        this.setState("ready");
      }
    }
  }
}

/**
 * Kill a process and everything it spawned.
 *
 * Synchronous on Windows on purpose: this runs during app quit, and an async
 * taskkill could still be starting when Electron exits -- leaving exactly the
 * orphan it was meant to prevent.
 */
function killTree(child: ChildProcess): void {
  if (child.pid === undefined || child.exitCode !== null) return;

  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    return;
  }

  try {
    child.kill("SIGTERM");
  } catch {
    // Already gone.
  }
}
