import { Database, UserRound } from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";

import type { PersonalMemoryState } from "@/hooks/usePersonalMemory";
import { MemorySettingsPanel, ProfileSettings } from "./PersonalMemorySettings";

const sections = [
  { id: "profile", label: "Your Profile", icon: UserRound },
  { id: "memory", label: "Memory and Context", icon: Database },
] as const;
type SystemTab = (typeof sections)[number]["id"];

export function SystemControls({ personalMemory }: { personalMemory: PersonalMemoryState }) {
  const [tab, setTab] = useState<SystemTab>("profile");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const section = sections.find((item) => item.id === tab)!;

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % sections.length;
    else if (event.key === "ArrowLeft") next = (index + sections.length - 1) % sections.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = sections.length - 1;
    else return;
    event.preventDefault();
    setTab(sections[next].id);
    tabs.current[next]?.focus();
  }

  return (
    <section className="settings-page panel min-h-0 flex-1 overflow-y-auto" aria-labelledby="system-controls-title">
      <div className="settings-content">
        <header className="settings-header">
          <div>
            <p className="label-caps">Shape how IRA knows you</p>
            <h1 id="system-controls-title" className="mt-2 text-[28px] font-semibold tracking-tight">System Controls</h1>
          </div>
          <span className="settings-section-tag"><section.icon size={15} /> {section.label}</span>
        </header>
        <div className="settings-tabs" role="tablist" aria-label="System Controls sections">
          {sections.map(({ id, label, icon: Icon }, index) => (
            <button
              key={id}
              ref={(element) => { tabs.current[index] = element; }}
              id={`system-tab-${id}`}
              type="button"
              role="tab"
              aria-selected={tab === id}
              aria-controls={`system-panel-${id}`}
              tabIndex={tab === id ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={(event) => navigateTabs(event, index)}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
        {/* Keep both drafts mounted while switching between profile and memory. */}
        <div id="system-panel-profile" role="tabpanel" aria-labelledby="system-tab-profile" hidden={tab !== "profile"}>
          <ProfileSettings memory={personalMemory} />
        </div>
        <div id="system-panel-memory" role="tabpanel" aria-labelledby="system-tab-memory" hidden={tab !== "memory"}>
          <MemorySettingsPanel memory={personalMemory} />
        </div>
      </div>
    </section>
  );
}
