// Isolated synthetic visual fixture. Never imported by the production dashboard.
import { createRoot } from "react-dom/client";
import { Stage } from "../src/components/Stage";
import { Backdrop } from "../src/components/Backdrop";
import { ComputerUsePreview } from "../src/components/ComputerUsePreview";
import { parseComputerObservation } from "../shared/computerUse";
import { EMPTY_COMPUTER_USE, trackComputerUse } from "../src/lib/computerUse";
import { applyTheme } from "../src/lib/themes";
import "../src/styles/theme.css";

applyTheme("blue");
const canvas = document.createElement("canvas");
canvas.width = 800; canvas.height = 450;
const ctx = canvas.getContext("2d")!;
ctx.fillStyle = "#e9eef3"; ctx.fillRect(0, 0, 800, 450);
ctx.fillStyle = "#1b2331"; ctx.fillRect(0, 0, 800, 46);
ctx.fillStyle = "#f4f7fc"; ctx.font = "16px Arial"; ctx.fillText("Synthetic test window · no real desktop data", 24, 29);
ctx.fillStyle = "#172b45"; ctx.font = "26px Arial"; ctx.fillText("Observed control", 72, 133);
ctx.fillStyle = "#fff"; ctx.fillRect(72, 164, 656, 166);
ctx.fillStyle = "#e2eaf4"; ctx.fillRect(100, 192, 434, 19); ctx.fillRect(100, 231, 350, 15);
ctx.fillStyle = "#167996"; ctx.fillRect(550, 280, 150, 42);
ctx.fillStyle = "#fff"; ctx.font = "16px Arial"; ctx.fillText("Continue", 592, 307);
const dataUrl = canvas.toDataURL("image/png");
const at = Date.now();
const create = (sequence: number, phase: string, extra = {}) => parseComputerObservation({ type: "computer.observation", run_id: "qa-run", call_id: "qa-call", tool_name: "desktop_click", sequence, phase, observation_id: `qa-${sequence}`, captured_at: new Date(at).toISOString(), image_size: [800, 450], screenshot_data_url: dataUrl, targets: [{ id: "button", name: "Continue", role: "button", image_bounds: [550, 280, 150, 42] }], cursor_image_position: [625, 301], ...extra })!;
let model = trackComputerUse(EMPTY_COMPUTER_USE, create(1, "before", { action: { action: "drag", observation_id: "qa-1", image_points: [[160, 210], [625, 301]], target: { id: "button", name: "Continue", role: "button", image_bounds: [550, 280, 150, 42] } } }), at);
model = trackComputerUse(model, create(2, "after"), at + 400);
const root = createRoot(document.getElementById("root")!);
const params = new URLSearchParams(location.search);
const mode = params.get("mode") || "stage";
const state = mode === "empty" ? EMPTY_COMPUTER_USE : model;
const now = mode === "stale" ? at + 60_000 : at + 800;
const connected = mode !== "offline";
const activities = [{ id: "qa", runId: "qa-run", callId: "qa-call", toolName: "desktop_click", title: "Selecting a desktop control", phase: "verifying" as const, startedAt: at, updatedAt: at + 400, endedAt: null }];
root.render(<div className="ira-dashboard relative flex h-full flex-col overflow-hidden"><Backdrop theme="blue" />
  <div style={{ position: "relative", zIndex: 10, height: "100vh", display: "flex", flexDirection: "column", padding: "30px 40px" }}>
    <p style={{ color: "var(--color-dim)", fontSize: 12, margin: "0 0 15px" }}>Synthetic computer use renderer QA</p>
    {mode === "standalone" ? <div style={{ width: 370 }}><ComputerUsePreview model={state} connected={connected} now={now} animate={false} activities={activities} /></div> : <Stage state={connected ? "executing" : "offline"} cues={[]} animate={false} getLevel={() => 0} activities={connected ? activities : []} computerUse={state} now={now} onClearExecution={() => {}} />}
  </div>
</div>);
