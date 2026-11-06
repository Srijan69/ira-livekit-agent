export type ThemeId = "blue" | "silver" | "red" | "pink" | "gold" | "green";

export type Theme = {
  id: ThemeId;
  name: string;
  description: string;
  accent: string;
  secondary: string;
  core: string;
  deep: string;
  surface: string;
  dim: string;
};

/** Palette choices follow the six supplied command-deck references. */
export const THEMES: readonly Theme[] = [
  {
    id: "blue",
    name: "Holographic Blue",
    description: "Cool cyan. The original command deck.",
    accent: "#35cfff",
    secondary: "#2478f5",
    core: "#d9f8ff",
    deep: "#030b14",
    surface: "#0b2135",
    dim: "#a2bdd2",
  },
  {
    id: "silver",
    name: "Arctic Silver",
    description: "White light. Polished titanium.",
    accent: "#e3ecff",
    secondary: "#9eafcb",
    core: "#ffffff",
    deep: "#090b10",
    surface: "#242a35",
    dim: "#b9c1d0",
  },
  {
    id: "red",
    name: "Neon Red",
    description: "Crimson energy. After-dark focus.",
    accent: "#ff535e",
    secondary: "#ed2339",
    core: "#ffe7e9",
    deep: "#140508",
    surface: "#351016",
    dim: "#d7acb2",
  },
  {
    id: "pink",
    name: "Cyberpunk Pink",
    description: "Electric magenta. A different frequency.",
    accent: "#ff5de1",
    secondary: "#d624a9",
    core: "#ffe4fa",
    deep: "#110611",
    surface: "#31122e",
    dim: "#d3afd0",
  },
  {
    id: "gold",
    name: "Solar Gold",
    description: "Amber reflections. Golden-hour glow.",
    accent: "#ffda55",
    secondary: "#e5a91e",
    core: "#fff4d4",
    deep: "#100c04",
    surface: "#30250d",
    dim: "#cec19a",
  },
  {
    id: "green",
    name: "Neon Green",
    description: "Emerald light. A fresh perspective.",
    accent: "#49f787",
    secondary: "#12c85a",
    core: "#dcffe8",
    deep: "#031008",
    surface: "#0b301a",
    dim: "#a4cbb3",
  },
];

export const THEME_STORAGE_KEY = "ira.theme.v1";
const DEFAULT_THEME: ThemeId = "blue";

export function getTheme(id: ThemeId): Theme {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}

export function readThemePreference(
  storage?: Pick<Storage, "getItem">,
): ThemeId {
  try {
    const saved = (storage ?? window.localStorage).getItem(THEME_STORAGE_KEY);
    return THEMES.find((theme) => theme.id === saved)?.id ?? DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function rgb(hex: string): string {
  return [1, 3, 5]
    .map((offset) => parseInt(hex.slice(offset, offset + 2), 16))
    .join(" ");
}

/** Run before mounting React so a saved theme never flashes blue on startup. */
export function applyTheme(id: ThemeId): void {
  const theme = getTheme(id);
  const root = document.documentElement;
  root.dataset.theme = theme.id;
  const tokens: Record<string, string> = {
    "--color-cyan": theme.accent,
    "--color-blue": theme.secondary,
    "--color-deep": theme.deep,
    "--color-mid": theme.deep,
    "--color-surface": theme.surface,
    "--color-text": theme.core,
    "--color-dim": theme.dim,
    "--color-mute": theme.dim,
    "--orb-ring": theme.accent,
    "--orb-deep": theme.secondary,
    "--orb-core": theme.core,
    "--accent-rgb": rgb(theme.accent),
    "--secondary-rgb": rgb(theme.secondary),
    "--surface-rgb": rgb(theme.surface),
    "--deep-rgb": rgb(theme.deep),
  };
  for (const [name, value] of Object.entries(tokens))
    root.style.setProperty(name, value);
}

/** Save before quitting; a rejected restart restores the previous preference. */
export async function restartWithTheme(
  id: ThemeId,
  restart: () => Promise<void>,
  storage: Pick<
    Storage,
    "getItem" | "setItem" | "removeItem"
  > = window.localStorage,
): Promise<void> {
  const previous = storage.getItem(THEME_STORAGE_KEY);
  storage.setItem(THEME_STORAGE_KEY, id);
  try {
    await restart();
  } catch (error) {
    if (previous === null) storage.removeItem(THEME_STORAGE_KEY);
    else storage.setItem(THEME_STORAGE_KEY, previous);
    throw error;
  }
}
