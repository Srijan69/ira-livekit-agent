import { createServer } from "node:net";

/**
 * Find a free TCP port by binding one and letting the OS choose.
 *
 * Electron picks the bridge's port rather than the bridge picking its own and
 * printing it: parsing a child's stdout for a port number is fragile, and
 * main needs the value authoritatively to proxy requests to it.
 *
 * There is a race in principle -- something else could take the port between
 * the probe and the child binding it -- but on loopback, over milliseconds,
 * it beats the alternatives.
 */
export function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address && typeof address === "object") {
        const { port } = address;
        server.close(() => resolve(port));
      } else {
        server.close(() => reject(new Error("could not determine a free port")));
      }
    });
  });
}
