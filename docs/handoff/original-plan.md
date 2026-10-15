# IRA Desktop Dashboard: Build Plan

Turning the reference dashboard image into a real desktop app, connected to an existing LiveKit backend.

A screenshot can't be converted directly into an app. The approach is to rebuild it as a web UI (HTML/CSS/JS) and wrap it in a desktop shell.

---

## 1. Recommended Stack

| Layer | Choice | Why |
|---|---|---|
| Desktop shell | **Electron** | Frameless window, tray, global hotkeys, system stats, all in JavaScript. (Tauri is lighter but needs Rust.) |
| UI framework | **React + Vite + Tailwind** | Fast dev loop, easy glassmorphism styling |
| Animation | **Framer Motion** (+ SVG/Canvas, or Three.js for the orb) | Smooth glowing rings and transitions |
| Voice / agent | **`livekit-client` + `@livekit/components-react`** | Official SDKs, work directly with your LiveKit agent |
| System stats | **`systeminformation`** (npm) | CPU, RAM, storage, battery |
| Weather | **Open-Meteo API** | Free, no API key |
| Packaging | **electron-builder** | Installers for Windows / macOS / Linux |

---

## 2. Mapping the Image to Real Components

| In the image | How to build it |
|---|---|
| Frameless glass window with custom close/minimize buttons | Electron `frame: false` (optionally `transparent: true`) + CSS `backdrop-filter: blur()` |
| Left sidebar (Home, Chat, Voice, Tasks, ...) | React sidebar component with route-based pages |
| Greeting + date/time header | React component with `Intl.DateTimeFormat` and a clock hook |
| Central IRA orb with "Listening..." and waveform | Animated SVG rings. Agent state from LiveKit's `useVoiceAssistant()` (listening / thinking / speaking), waveform from the audio track amplitude |
| "Online" badge | LiveKit room connection state |
| Mic button | `room.localParticipant.setMicrophoneEnabled(true/false)` |
| System Status rings (CPU/RAM/Storage/Battery) | `systeminformation` in the Electron main process, sent to the UI over IPC every 1-2 seconds |
| Weather card | Open-Meteo fetch, cached |
| Upcoming Tasks | Local store (SQLite or JSON) first; later the agent creates tasks via LiveKit RPC / data messages |
| Quick action cards (Chat, Web Search, Code Assist, ...) | Button grid that sends a prompt or mode switch to the agent |
| "Ask IRA anything..." input bar | Text input that sends a message to the agent over a LiveKit data channel or text stream |
| Suggestion chips (Summarize this PDF, Open VS Code, ...) | Chip components that pre-fill or send prompts |
| Background city / Earth scene | Do **not** use the screenshot. Use a clean generated background (no UI baked in), or a CSS gradient / looping video |

---

## 3. How It Connects to the LiveKit Backend

1. **Token endpoint:** a small FastAPI (Python) route that returns a LiveKit access token for a room.
2. **Join the room:** the app fetches the token, connects with `livekit-client`, publishes the mic, and plays the agent's audio track.
3. **Agent state to UI:** the orb and "Listening..." / "Thinking..." / "Speaking..." label follow the agent's state.
4. **Non-voice features** (tasks, automation, system control): the agent talks to the UI through LiveKit **data messages or RPC**. Example: the agent calls an `open_app` RPC and the Electron main process launches VS Code.

```
 ┌────────────────────┐        WebRTC         ┌─────────────────────┐
 │  Electron + React  │ <───────────────────> │   LiveKit Server    │
 │  (IRA Dashboard)   │                       └──────────┬──────────┘
 └─────────┬──────────┘                                  │
           │ IPC                                         │
 ┌─────────▼──────────┐                       ┌──────────▼──────────┐
 │  Electron main     │                       │  Python LiveKit     │
 │  (system stats,    │                       │  Agent (STT/LLM/TTS)│
 │   launch apps)     │                       └─────────────────────┘
 └────────────────────┘
```

---

## 4. Suggested Project Structure

```
ira-dashboard/
├─ electron/
│  ├─ main.ts            # window creation, tray, global hotkey
│  ├─ preload.ts         # safe IPC bridge (contextBridge)
│  └─ system.ts          # systeminformation polling, app launching
├─ src/
│  ├─ main.tsx
│  ├─ App.tsx
│  ├─ components/
│  │  ├─ Sidebar.tsx
│  │  ├─ Header.tsx          # greeting + clock
│  │  ├─ IraOrb.tsx          # animated core + waveform
│  │  ├─ SystemStatus.tsx
│  │  ├─ WeatherCard.tsx
│  │  ├─ UpcomingTasks.tsx
│  │  ├─ QuickActions.tsx
│  │  └─ CommandBar.tsx      # input + suggestion chips
│  ├─ hooks/
│  │  ├─ useLiveKitAgent.ts  # connect, mic, agent state
│  │  └─ useSystemStats.ts
│  ├─ lib/
│  │  └─ token.ts            # fetch token from FastAPI
│  └─ styles/
│     └─ theme.css           # colors, glass, glow variables
├─ package.json
└─ electron-builder.yml
```

---

## 5. Build Order

1. **Skeleton:** Electron + Vite + React, frameless window, static dashboard layout matching the image.
2. **Voice:** LiveKit connection, working mic toggle, orb reacting to agent state and audio.
3. **Widgets:** system stats, weather, tasks.
4. **Actions:** quick-action cards, command bar, automation via RPC (open apps, system control).
5. **Polish:** tray icon, global hotkey (e.g. `Ctrl+Space`), always-on-top / overlay mode, startup on boot.
6. **Package:** `electron-builder` installer.

---

## 6. Key Design Notes

- **Theme:** dark navy base, cyan/blue glow accents, glass panels (`background: rgba(...)` + `backdrop-filter: blur(20px)` + 1px translucent border).
- **Security:** keep `contextIsolation: true` and expose only specific IPC methods through `preload.ts`. Never give the renderer direct Node access, especially since the agent can trigger system actions.
- **Performance:** poll system stats every 1-2 s, not faster. Pause heavy animations when the window is hidden.
- **Token safety:** never put the LiveKit API secret in the app. Only the token server holds it.

---

## 7. Open Questions

- Target OS (Windows / macOS / Linux)? This affects packaging and system-control commands.
- Should the window be a normal app window, or a transparent always-on-top overlay?
- Where should tasks live: local only, or synced with the backend?
