import type { Session } from "@/lib/session";

import { useLiveKitSession } from "./useLiveKitSession";
import { useMockSession } from "./useMockSession";

/**
 * Which session the app uses.
 *
 * Chosen once, at module load, rather than per render -- swapping which hook
 * is called between renders breaks the rules of hooks, and the choice is a
 * build-time constant anyway.
 *
 * The browser-only dev server (`npm run dev:web`) has no Electron bridge and
 * therefore no way to mint a token, so it runs the scripted mock and stays
 * useful for layout and animation work. The Electron app always uses the real
 * one.
 */
export const useSession: () => Session =
  import.meta.env.VITE_IRA_MOCK === "1" ? useMockSession : useLiveKitSession;

export type { Session };
