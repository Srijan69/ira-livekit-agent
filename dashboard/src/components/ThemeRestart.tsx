import { Power, RotateCw } from "lucide-react";

import { getTheme, type ThemeId } from "@/lib/themes";

export function ThemeRestart({ theme }: { theme: ThemeId }) {
  const selected = getTheme(theme);
  return (
    <div className="theme-restart-screen" role="status" aria-live="polite">
      <Power size={38} strokeWidth={1.25} className="text-cyan" />
      <p className="mt-6 text-[11px] font-medium tracking-[0.3em] text-dim">
        A NEW ATMOSPHERE
      </p>
      <h1 className="mt-3 text-[30px] font-semibold tracking-tight">
        Restarting IRA
      </h1>
      <p className="mt-3 text-sm text-dim">Coming back in {selected.name}.</p>
      <RotateCw size={18} className="mt-8 animate-spin text-cyan" aria-hidden />
    </div>
  );
}
