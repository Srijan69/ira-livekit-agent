import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

/** A successful request stays latched until process exit. Failures allow retry. */
export function createRestartController(actions: {
  flushStorage: () => void;
  prepareRelaunch: () => Promise<void>;
  quit: () => void;
}): () => Promise<void> {
  let pending: Promise<void> | null = null;
  return () => {
    if (pending) return pending;
    pending = (async () => {
      actions.flushStorage();
      await actions.prepareRelaunch();
      actions.quit();
    })().catch((error: unknown) => {
      pending = null;
      throw error;
    });
    return pending;
  };
}

// electron-vite exits when its Electron child closes. A hidden Node handoff
// waits for both old processes before starting the dev server on the SAME
// origin, which preserves Chromium's localStorage theme preference.
const DEV_HANDOFF = String.raw`
const config = JSON.parse(process.argv[1]);
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const deadline = Date.now() + 60000;
const timer = setInterval(async () => {
  if (config.pids.some(alive)) {
    if (Date.now() > deadline) process.exit(1);
    return;
  }
  clearInterval(timer);
  try {
    const { createServer } = await import(config.vite);
    await createServer({ root: config.root, server: config.server }, { rendererOnly: false });
  } catch { process.exit(1); }
}, 200);
`;

export async function prepareDevelopmentRelaunch(
  root: string,
  rendererUrl: string,
): Promise<void> {
  const node = process.env["npm_node_execpath"];
  const vite = join(root, "node_modules/electron-vite/dist/index.js");
  if (!node || !existsSync(node) || !existsSync(vite)) {
    throw new Error(
      "Start the development app with npm run dev to enable restarting.",
    );
  }
  const url = new URL(rendererUrl);
  const env = { ...process.env };
  delete env["ELECTRON_RENDERER_URL"];
  delete env["ELECTRON_RUN_AS_NODE"];
  const config = {
    root,
    vite: pathToFileURL(vite).href,
    pids: [process.pid, process.ppid],
    server: { host: url.hostname, port: Number(url.port), strictPort: true },
  };
  await new Promise<void>((resolve, reject) => {
    const handoff = spawn(node, ["-e", DEV_HANDOFF, JSON.stringify(config)], {
      cwd: root,
      env,
      windowsHide: true,
      detached: true,
      stdio: "ignore",
    });
    handoff.once("error", reject);
    handoff.once("spawn", () => {
      handoff.unref();
      resolve();
    });
  });
}
