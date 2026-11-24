export const COMPUTER_OBSERVATION_TOPIC = "computer.observation";
export const COMPUTER_OBSERVATION_MAX_BYTES = 512 * 1024;
export type ImagePoint = [number, number];
export type ImageBounds = [number, number, number, number];
export type ComputerTarget = { id: string; name: string; role: string; image_bounds: ImageBounds };
export type ComputerWindow = { id: string; title: string; image_bounds: ImageBounds };
export type ComputerAction = {
  action: "move" | "click" | "click_target" | "scroll" | "drag" | "type" | "key" | "focus";
  observation_id: string;
  image_points: ImagePoint[];
  target?: ComputerTarget;
  window?: ComputerWindow;
};
export type ComputerObservationEvent = {
  type: "computer.observation";
  run_id: string;
  call_id: string;
  tool_name: string;
  sequence: number;
  phase: "observed" | "before" | "after" | "unavailable" | "cleared";
  observation_id?: string;
  captured_at?: string;
  image_size?: ImagePoint;
  screenshot_data_url?: string;
  targets?: ComputerTarget[];
  windows?: ComputerWindow[];
  cursor_image_position?: ImagePoint;
  action?: ComputerAction;
};
const PHASES = new Set(["observed", "before", "after", "unavailable", "cleared"]);
const ACTIONS = new Set(["move", "click", "click_target", "scroll", "drag", "type", "key", "focus"]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const label = (value: unknown, limit = 240): value is string => typeof value === "string" && value.length <= limit && !/[\u0000-\u001f\u007f]/.test(value);
const identifier = (value: unknown): value is string => label(value, 128) && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function point(value: unknown, size: ImagePoint): ImagePoint | null {
  return Array.isArray(value) && value.length === 2 && value.every(finite)
    && value[0] >= 0 && value[1] >= 0 && value[0] < size[0] && value[1] < size[1]
    ? [value[0], value[1]] : null;
}

function bounds(value: unknown, size: ImagePoint): ImageBounds | null {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(finite)
    || value[2] <= 0 || value[3] <= 0 || value.some((n) => Math.abs(n) > 100_000)) return null;
  const x = Math.max(0, value[0]), y = Math.max(0, value[1]);
  const right = Math.min(size[0], value[0] + value[2]), bottom = Math.min(size[1], value[1] + value[3]);
  return right > x && bottom > y ? [x, y, right - x, bottom - y] : null;
}

function target(value: unknown, size: ImagePoint): ComputerTarget | null {
  if (!record(value) || !identifier(value.id) || !label(value.name) || !label(value.role, 80)) return null;
  const box = bounds(value.image_bounds, size);
  return box ? { id: value.id, name: value.name, role: value.role, image_bounds: box } : null;
}

function windowMetadata(value: unknown, size: ImagePoint): ComputerWindow | null {
  if (!record(value) || !identifier(value.id) || !label(value.title)) return null;
  const box = bounds(value.image_bounds, size);
  return box ? { id: value.id, title: value.title, image_bounds: box } : null;
}

/** Check declared MIME against encoded header and dimensions before rendering. */
function imageDimensions(url: string): ImagePoint | null {
  const match = /^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})$/.exec(url);
  if (!match || match[2].length % 4 !== 0 || url.length > COMPUTER_OBSERVATION_MAX_BYTES) return null;
  let bytes: Uint8Array;
  try { bytes = Uint8Array.from(atob(match[2]), (character) => character.charCodeAt(0)); } catch { return null; }
  if (match[1] === "png") {
    const signature = [137, 80, 78, 71, 13, 10, 26, 10];
    if (bytes.length < 33 || signature.some((n, i) => bytes[i] !== n)
      || String.fromCharCode(...bytes.subarray(12, 16)) !== "IHDR") return null;
    const view = new DataView(bytes.buffer);
    return [view.getUint32(16), view.getUint32(20)];
  }
  if (bytes.length < 4 || bytes[0] !== 255 || bytes[1] !== 216 || bytes.at(-2) !== 255 || bytes.at(-1) !== 217) return null;
  let offset = 2;
  while (offset + 4 < bytes.length) {
    if (bytes[offset++] !== 255) return null;
    while (bytes[offset] === 255) offset++;
    const marker = bytes[offset++];
    if (marker === 217 || marker === 218) break;
    if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
    const length = (bytes[offset] << 8) | bytes[offset + 1];
    if (length < 2 || offset + length > bytes.length) return null;
    if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker)) {
      if (length < 8) return null;
      return [(bytes[offset + 5] << 8) | bytes[offset + 6], (bytes[offset + 3] << 8) | bytes[offset + 4]];
    }
    offset += length;
  }
  return null;
}

/** Rebuild allowlisted fields: input text, raw results and errors never cross this boundary. */
export function parseComputerObservation(value: unknown): ComputerObservationEvent | null {
  if (!record(value) || value.type !== COMPUTER_OBSERVATION_TOPIC
    || ![value.run_id, value.call_id, value.tool_name].every(identifier)
    || typeof value.sequence !== "number" || !Number.isSafeInteger(value.sequence) || value.sequence < 0
    || typeof value.phase !== "string" || !PHASES.has(value.phase)) return null;
  const base: ComputerObservationEvent = {
    type: COMPUTER_OBSERVATION_TOPIC, run_id: value.run_id as string, call_id: value.call_id as string,
    tool_name: value.tool_name as string, sequence: value.sequence, phase: value.phase as ComputerObservationEvent["phase"],
  };
  if (base.phase === "cleared" || base.phase === "unavailable") return base;
  if (!identifier(value.observation_id) || typeof value.captured_at !== "string"
    || !/^\d{4}-\d{2}-\d{2}T/.test(value.captured_at) || !Number.isFinite(Date.parse(value.captured_at))
    || !Array.isArray(value.image_size) || value.image_size.length !== 2
    || !value.image_size.every((n) => Number.isInteger(n) && n > 0 && n <= 8192)
    || value.image_size[0] * value.image_size[1] > 24_000_000
    || typeof value.screenshot_data_url !== "string") return null;
  const size: ImagePoint = [value.image_size[0], value.image_size[1]];
  const actual = imageDimensions(value.screenshot_data_url);
  if (!actual || actual[0] !== size[0] || actual[1] !== size[1]) return null;
  const parsed: ComputerObservationEvent = {
    ...base, observation_id: value.observation_id, captured_at: value.captured_at,
    image_size: size, screenshot_data_url: value.screenshot_data_url,
  };
  if (value.targets !== undefined) {
    if (!Array.isArray(value.targets) || value.targets.length > 64) return null;
    parsed.targets = value.targets.map((entry) => target(entry, size)).filter((entry): entry is ComputerTarget => !!entry);
  }
  if (value.windows !== undefined) {
    if (!Array.isArray(value.windows) || value.windows.length > 16) return null;
    parsed.windows = value.windows.map((entry) => windowMetadata(entry, size)).filter((entry): entry is ComputerWindow => !!entry);
  }
  if (value.cursor_image_position !== undefined) {
    const cursor = point(value.cursor_image_position, size);
    if (!cursor) return null;
    parsed.cursor_image_position = cursor;
  }
  if (value.action !== undefined) {
    const action = value.action;
    if (!record(action) || typeof action.action !== "string" || !ACTIONS.has(action.action)
      || action.observation_id !== parsed.observation_id || !Array.isArray(action.image_points) || action.image_points.length > 8) return null;
    const points = action.image_points.map((entry) => point(entry, size));
    if (points.some((entry) => !entry)) return null;
    parsed.action = { action: action.action as ComputerAction["action"], observation_id: action.observation_id as string, image_points: points as ImagePoint[] };
    const selected = target(action.target, size), focused = windowMetadata(action.window, size);
    if (selected) parsed.action.target = selected;
    if (focused) parsed.action.window = focused;
  }
  return parsed;
}
