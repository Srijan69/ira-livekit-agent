import { ChevronDown, Expand, Monitor, MousePointer2, Scan, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { COMPUTER_ACTION_LABELS, computerUseStatus, type ComputerFrame, type ComputerUseState } from "@/lib/computerUse";
import { safeToolName, type ExecutionActivity } from "@/lib/execution";

const frameLabel = (frame: ComputerFrame) => frame.event.phase === "before" ? "Before action" : frame.event.phase === "after" ? "After action" : "Observed screen";

function ObservedImage({ frame, outlines, onFailure, large = false }: { frame: ComputerFrame; outlines: boolean; onFailure: () => void; large?: boolean }) {
  const event = frame.event;
  const [width, height] = event.image_size!;
  const action = event.action;
  const markerSize = Math.min(width, height) / 45;
  return (
    <div className="computer-image" style={{ aspectRatio: `${width} / ${height}`, maxWidth: large ? `min(100%, calc((100dvh - 160px) * ${width / height}))` : undefined }}>
      <img src={event.screenshot_data_url} alt={`Observed desktop · ${frameLabel(frame).toLowerCase()}`} draggable={false}
        onError={onFailure} onLoad={(e) => {
          const image = e.currentTarget;
          if (image.naturalWidth !== width || image.naturalHeight !== height) onFailure();
        }} />
      <svg className="computer-image-overlay" viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
        {outlines && event.targets?.map((target, index) => (
          <g key={`${target.id}-${index}`} data-target-id={target.id} data-selected={target.id === action?.target?.id}>
            <rect className="computer-target" x={target.image_bounds[0]} y={target.image_bounds[1]} width={target.image_bounds[2]} height={target.image_bounds[3]} />
          </g>
        ))}
        {outlines && action?.target && !event.targets?.some((target) => target.id === action.target?.id) &&
          <g data-target-id={action.target.id} data-selected="true"><rect className="computer-target" x={action.target.image_bounds[0]} y={action.target.image_bounds[1]} width={action.target.image_bounds[2]} height={action.target.image_bounds[3]} /></g>}
        {outlines && action?.window && <rect className="computer-window-target" x={action.window.image_bounds[0]} y={action.window.image_bounds[1]} width={action.window.image_bounds[2]} height={action.window.image_bounds[3]} />}
        {action && <g data-action-marker={action.action} className="computer-action-marker">
          {action.image_points.length > 1 && <polyline className="computer-action-path" points={action.image_points.map((point) => point.join(",")).join(" ")} />}
          {action.image_points.map(([x, y], index) => <g key={index}>
            <circle className="computer-action-ring" cx={x} cy={y} r={markerSize} />
            <circle className="computer-action-center" cx={x} cy={y} r={markerSize / 4} />
          </g>)}
        </g>}
        {event.cursor_image_position && <g className="computer-cursor" transform={`translate(${event.cursor_image_position.join(" ")})`}>
          <path d={`M0 0 L0 ${markerSize * 1.6} L${markerSize * .5} ${markerSize * 1.1} L${markerSize * 1.1} ${markerSize * 1.1} Z`} />
        </g>}
      </svg>
    </div>
  );
}

export function ComputerUsePreview({ model, connected, now, animate, activities }: {
  model: ComputerUseState; connected: boolean; now: number; animate: boolean; activities: ExecutionActivity[];
}) {
  const [expanded, setExpanded] = useState(true);
  const [showBefore, setShowBefore] = useState(false);
  const [outlines, setOutlines] = useState(true);
  const [enlarged, setEnlarged] = useState(false);
  const [failedFrame, setFailedFrame] = useState<string | null>(null);
  const enlargeButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const id = useId();
  const selected = connected ? showBefore && model.before ? model.before : model.latest : null;
  const selectedKey = selected ? `${selected.event.run_id}-${selected.event.sequence}` : "";
  const failed = !!selected && failedFrame === selectedKey;
  const status = computerUseStatus({ ...model, latest: selected }, connected, now);
  const age = selected ? Math.max(0, Math.floor((now - Date.parse(selected.event.captured_at!)) / 1_000)) : 0;
  const paired = !!model.before && !!model.latest && model.before.event.sequence !== model.latest.event.sequence;
  const statusLabel = !connected ? "Disconnected" : failed ? "Image unavailable" : status === "stale" ? "Last observed" : status === "unavailable" ? "Observation unavailable" : status === "empty" ? "No screen yet" : "Fresh capture";
  const canEnlarge = !!selected && !failed && expanded;
  const modalOpen = enlarged && canEnlarge;
  useEffect(() => { if (!canEnlarge) setEnlarged(false); }, [canEnlarge]);

  useEffect(() => {
    if (!modalOpen) return;
    closeButton.current?.focus();
    return () => { enlargeButton.current?.focus(); };
  }, [modalOpen]);

  const image = selected && !failed ? <ObservedImage frame={selected} outlines={outlines} onFailure={() => setFailedFrame(selectedKey)} /> : null;
  const selectedName = selected?.event.action?.target?.name || selected?.event.action?.window?.title;
  return (
    <section className="computer-preview" data-status={failed ? "unavailable" : status} data-animate={animate} aria-label="Computer use">
      <div className="computer-preview-heading">
        <button type="button" className="computer-preview-toggle" aria-expanded={expanded} aria-controls={`computer-content-${id}`} onClick={() => setExpanded((value) => !value)}>
          <Monitor size={12} aria-hidden="true" /><span>Computer use</span><ChevronDown size={12} aria-hidden="true" />
        </button>
        <span className="computer-freshness">{statusLabel}</span>
      </div>
      <div id={`computer-content-${id}`} className="computer-preview-content" hidden={!expanded}>
        {selected ? <>
          <div className="computer-frame-heading">
            {paired ? <div className="computer-frame-switch" aria-label="Observed frames">
              <button type="button" aria-pressed={showBefore} onClick={() => setShowBefore(true)}>Before</button>
              <button type="button" aria-pressed={!showBefore} onClick={() => setShowBefore(false)}>After</button>
            </div> : <span>{frameLabel(selected)}</span>}
            <div className="computer-image-tools">
              {!failed && <button type="button" aria-label="Show target outlines" aria-pressed={outlines} onClick={() => setOutlines((value) => !value)}><Scan size={12} aria-hidden="true" /></button>}
              {canEnlarge && <button type="button" ref={enlargeButton} aria-label="Enlarge observed screen" onClick={() => setEnlarged(true)}><Expand size={12} aria-hidden="true" /></button>}
            </div>
          </div>
          {failed ? <p className="computer-empty">Screen image unavailable</p> : image}
          <div className="computer-image-caption"><span>{selectedName || "Observed desktop"}</span><time dateTime={selected.event.captured_at} title={selected.event.captured_at}>{age}s ago</time></div>
          {(status === "stale" || status === "unavailable") && <p className="computer-stale-note">Screen may have changed. Waiting for a fresh observation.</p>}
        </> : <p className="computer-empty">{connected ? model.phase === "unavailable" ? "Screen observation unavailable. Waiting for another capture." : "No screen observation yet. Captures appear as IRA uses your computer." : "Connect to see IRA's screen observations."}</p>}
        {connected && model.timeline.length > 0 && <ol className="computer-action-timeline" aria-label="Computer actions">
          {model.timeline.slice(0, 3).map((entry) => {
            const execution = activities.find((activity) => activity.runId === model.runId && activity.callId === entry.callId);
            const label = entry.action ? COMPUTER_ACTION_LABELS[entry.action.action] : "Reviewing screen";
            const detail = execution?.phase === "failed" ? "Action failed" : execution?.phase === "cancelled" ? "Cancelled" : entry.phase === "after" ? "Result observed" : entry.phase === "before" ? "Action requested" : entry.phase === "unavailable" ? "Observation unavailable" : "Screen observed";
            return <li key={entry.callId}><MousePointer2 size={10} aria-hidden="true" /><div><span>{label}</span><small>{detail}{safeToolName(entry.toolName) ? ` · ${entry.toolName}` : ""}</small></div></li>;
          })}
        </ol>}
      </div>
      {modalOpen && createPortal(<div className="computer-screen-backdrop" onClick={(event) => { if (event.target === event.currentTarget) setEnlarged(false); }}>
        <div className="computer-screen-modal" role="dialog" aria-modal="true" aria-labelledby={`computer-modal-title-${id}`}
          onKeyDown={(event) => {
            if (event.key === "Escape") { event.preventDefault(); setEnlarged(false); }
            if (event.key === "Tab") { event.preventDefault(); closeButton.current?.focus(); }
          }}>
          <header><div><h2 id={`computer-modal-title-${id}`}>Observed screen</h2><p>{selected ? frameLabel(selected) : ""} · {statusLabel} · {age}s ago</p></div><button ref={closeButton} type="button" aria-label="Close observed screen" onClick={() => setEnlarged(false)}><X size={18} aria-hidden="true" /></button></header>
          {selected && !failed && <ObservedImage frame={selected} outlines={outlines} large onFailure={() => setFailedFrame(selectedKey)} />}
          {selectedName && <p className="computer-modal-caption">{selectedName}</p>}
        </div>
      </div>, document.body)}
    </section>
  );
}
