import { THEME_ASSETS } from "@/lib/themeAssets";
import type { ThemeId } from "@/lib/themes";

/** User-supplied scene, bundled locally so the desktop app works offline. */
export function Backdrop({ theme = "blue" }: { theme?: ThemeId }) {
  return (
    <div
      aria-hidden
      className="observatory pointer-events-none absolute inset-0 overflow-hidden"
    >
      <img
        src={THEME_ASSETS[theme].deck}
        alt=""
        className="observatory-scene"
        draggable={false}
      />
      <div className="observatory-vignette" />
    </div>
  );
}
