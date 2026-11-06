// @vitest-environment node
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync, rmdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { describe, expect, it, vi } from "vitest";

const { spawnMock } = vi.hoisted(() => ({ spawnMock: vi.fn() }));
vi.mock("node:child_process", () => ({ spawn: spawnMock }));

import { createRestartController, prepareDevelopmentRelaunch } from "./restart";

describe("desktop restart", () => {
  it("flushes saved preferences and prepares the relaunch before requesting shutdown", async () => {
    const events: string[] = [];
    const restart = createRestartController({
      flushStorage: () => {
        events.push("flush");
      },
      prepareRelaunch: async () => {
        events.push("relaunch");
      },
      quit: () => {
        events.push("quit");
      },
    });
    await restart();
    expect(events).toEqual(["flush", "relaunch", "quit"]);
  });

  it("schedules only one relaunch when several requests arrive", async () => {
    let release!: () => void;
    const prepareRelaunch = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const quit = vi.fn();
    const restart = createRestartController({
      flushStorage: vi.fn(),
      prepareRelaunch,
      quit,
    });
    const first = restart();
    const second = restart();
    release();
    await Promise.all([first, second]);
    await restart();
    expect(prepareRelaunch).toHaveBeenCalledOnce();
    expect(quit).toHaveBeenCalledOnce();
  });

  it("stays open and allows retry when a relaunch cannot be prepared", async () => {
    const quit = vi.fn();
    const prepareRelaunch = vi
      .fn()
      .mockRejectedValueOnce(new Error("missing runtime"))
      .mockResolvedValue(undefined);
    const restart = createRestartController({
      flushStorage: vi.fn(),
      prepareRelaunch,
      quit,
    });
    await expect(restart()).rejects.toThrow("missing runtime");
    expect(quit).not.toHaveBeenCalled();
    await restart();
    expect(quit).toHaveBeenCalledOnce();
  });

  it("hands the original origin to electron-vite without overriding the renderer root", async () => {
    const root = process.cwd();
    const directory = mkdtempSync(join(tmpdir(), "ira-handoff-test-"));
    const shim = join(directory, "resolve-only.mjs");
    const child = Object.assign(new EventEmitter(), { unref: vi.fn() });
    spawnMock.mockImplementationOnce(() => {
      queueMicrotask(() => child.emit("spawn"));
      return child;
    });
    vi.stubEnv("npm_node_execpath", process.execPath);

    try {
      await prepareDevelopmentRelaunch(root, "http://127.0.0.1:54321");
      const [, args] = spawnMock.mock.calls[0] as [string, string[]];
      const config = JSON.parse(args[2]);
      const vite = config.vite;
      // Run the real generated helper, but substitute a module that only
      // resolves configuration. No server, Electron, or backend is started.
      config.pids = [];
      config.vite = pathToFileURL(shim).href;
      writeFileSync(shim, `
        import { resolveConfig } from ${JSON.stringify(vite)};
        export async function createServer(inlineConfig, options) {
          const { config } = await resolveConfig(inlineConfig, "serve");
          process.stdout.write(JSON.stringify({
            root: config.renderer.root,
            entry: config.renderer.build.rollupOptions.input,
            server: config.renderer.server,
            rendererOnly: options.rendererOnly,
          }));
        }
      `);
      const { spawnSync } = await vi.importActual<typeof import("node:child_process")>("node:child_process");
      const result = spawnSync(process.execPath, [args[0], args[1], JSON.stringify(config)], {
        cwd: root,
        encoding: "utf8",
        windowsHide: true,
        timeout: 8_000,
      });
      expect(result.status, result.stderr).toBe(0);
      expect(JSON.parse(result.stdout)).toEqual({
        root: "src",
        entry: join(root, "src/index.html"),
        server: { host: "127.0.0.1", port: 54321, strictPort: true },
        rendererOnly: false,
      });
    } finally {
      vi.unstubAllEnvs();
      spawnMock.mockReset();
      rmSync(shim, { force: true });
      rmdirSync(directory);
    }
  }, 10_000);
});
