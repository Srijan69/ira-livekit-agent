"""Bounded, ephemeral computer evidence over ordered LiveKit text streams."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
from collections import deque

from PIL import Image

logger = logging.getLogger(__name__)
COMPUTER_OBSERVATION_TOPIC = "computer.observation"
MAX_COMPUTER_EVENT_BYTES = 512 * 1024
_PHASES = {"observed", "before", "after", "unavailable", "cleared"}
_ACTIONS = {"move", "click", "click_target", "scroll", "drag", "type", "key", "focus"}


def _text(value, limit=128):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _point(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(type(v) in (int, float) and math.isfinite(v) for v in value):
        return None
    if not all(0 <= v < bound for v, bound in zip(value, size, strict=True)):
        return None
    return [value[0], value[1]]


def _box(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(type(v) in (int, float) and math.isfinite(v) for v in value):
        return None
    x, y, width, height = value
    if width <= 0 or height <= 0:
        return None
    left, top = max(0, x), max(0, y)
    right, bottom = min(size[0], x + width), min(size[1], y + height)
    if right <= left or bottom <= top:
        return None
    return [left, top, right - left, bottom - top]


def _target(value, size, *, window=False):
    if not isinstance(value, dict):
        return None
    identity = _text(value.get("id"))
    bounds = _box(value.get("image_bounds"), size)
    if not identity or bounds is None:
        return None
    if window:
        return {
            "id": identity,
            "title": _text(value.get("title"), 160),
            "image_bounds": bounds,
        }
    return {
        "id": identity,
        "name": _text(value.get("name"), 160),
        "role": _text(value.get("role"), 40),
        "image_bounds": bounds,
    }


def _image(description, size):
    raw = description.get("image_bytes")
    if not isinstance(raw, bytes):
        url = description.get("screenshot_data_url", "")
        if not isinstance(url, str) or len(url) > 4_000_000:
            return None
        if not url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
            return None
        try:
            raw = base64.b64decode(url.split(",", 1)[1], validate=True)
        except ValueError:
            return None
    if not 0 < len(raw) <= 3_000_000:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG"} or image.size != tuple(size):
                return None
            image.load()
            if len(raw) > 350_000:
                for quality in (75, 60, 45, 30):
                    output = io.BytesIO()
                    image.convert("RGB").save(output, "JPEG", quality=quality)
                    raw = output.getvalue()
                    if len(raw) <= 350_000:
                        break
                if len(raw) > 350_000:
                    return None
                mime = "image/jpeg"
            else:
                mime = "image/jpeg" if image.format == "JPEG" else "image/png"
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def build_computer_event(
    description, *, run_id, call_id, tool_name, sequence, phase, action=None
):
    """Rebuild safe metadata; never transmit typed text, logs, paths or args."""
    if phase not in _PHASES or type(sequence) is not int or sequence < 1:
        return None
    ids = [_text(value) for value in (run_id, call_id, tool_name)]
    if not all(ids):
        return None
    event = {
        "type": COMPUTER_OBSERVATION_TOPIC,
        "run_id": ids[0],
        "call_id": ids[1],
        "tool_name": ids[2],
        "sequence": sequence,
        "phase": phase,
    }
    if phase in {"cleared", "unavailable"}:
        return event
    if not isinstance(description, dict):
        return None
    size = description.get("image_size")
    if (
        not isinstance(size, (list, tuple))
        or len(size) != 2
        or not all(type(v) is int and 0 < v <= 4096 for v in size)
        or size[0] * size[1] > 8_294_400
    ):
        return None
    image = _image(description, size)
    identity = _text(description.get("observation_id"))
    captured_at = _text(description.get("captured_at"))
    if image is None or not identity or not captured_at:
        return None
    event.update(
        observation_id=identity,
        captured_at=captured_at,
        image_size=list(size),
        screenshot_data_url=image,
    )
    for key, limit in (("targets", 64), ("windows", 16)):
        source = description.get(key, [])
        if not isinstance(source, (tuple, list)):
            source = []
        event[key] = [
            target
            for entry in source[:limit]
            if (target := _target(entry, size, window=key == "windows")) is not None
        ]
    cursor = _point(description.get("cursor_image_position"), size)
    if cursor is not None:
        event["cursor_image_position"] = cursor
    if phase == "before" and isinstance(action, dict):
        if (
            action.get("action") not in _ACTIONS
            or action.get("observation_id") != identity
        ):
            return None
        points = action.get("image_points", [])
        if not isinstance(points, (list, tuple)) or len(points) > 2:
            return None
        valid_points = [_point(point, size) for point in points]
        if any(point is None for point in valid_points):
            return None
        safe_action = {
            "action": action["action"],
            "observation_id": identity,
            "image_points": valid_points,
        }
        for key in ("target", "window"):
            target = _target(action.get(key), size, window=key == "window")
            if target is not None:
                safe_action[key] = target
        event["action"] = safe_action
    if (
        len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
        > MAX_COMPUTER_EVENT_BYTES
    ):
        return None
    return event


class _ComputerPublisher:
    def __init__(self, room):
        self.room = room
        self.loop = asyncio.get_running_loop()
        self.pending = deque(maxlen=4)
        self.task = None
        self.send_task = None
        self.epoch = 0
        self.closed = False
        self.connection_handler = self._connection_changed
        self.listening = False
        if callable(getattr(room, "on", None)):
            room.on("connection_state_changed", self.connection_handler)
            self.listening = True

    def _connection_changed(self, *_):
        self.epoch += 1
        self.pending.clear()
        if self.send_task is not None:
            self.send_task.cancel()

    def _connected(self):
        try:
            return bool(self.room.isconnected())
        except Exception:
            return False

    def __call__(self, event):
        try:
            if self.closed or not self._connected():
                return
            epoch = self.epoch
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > MAX_COMPUTER_EVENT_BYTES:
                return
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self.loop:
                self._queue(epoch, payload)
            else:
                self.loop.call_soon_threadsafe(self._queue, epoch, payload)
        except Exception:
            logger.debug("Computer preview could not be scheduled", exc_info=True)

    def _queue(self, epoch, payload):
        if self.closed or not self._connected() or epoch != self.epoch:
            return
        self.pending.append((epoch, payload))
        if self.task is None or self.task.done():
            self.task = self.loop.create_task(self._drain())

    async def _drain(self):
        while self.pending:
            epoch, payload = self.pending.popleft()
            if epoch != self.epoch:
                continue
            if not self._connected():
                self.pending.clear()
                return
            try:
                self.send_task = self.loop.create_task(
                    self.room.local_participant.send_text(
                        payload, topic=COMPUTER_OBSERVATION_TOPIC
                    )
                )
                await asyncio.wait_for(self.send_task, timeout=5)
            except asyncio.CancelledError:
                if epoch == self.epoch:
                    raise
            except Exception:
                self.pending.clear()
                logger.debug("Computer preview stream failed", exc_info=True)
            finally:
                self.send_task = None

    async def aclose(self):
        await asyncio.sleep(0)  # Admit already-scheduled worker-thread events.
        self.closed = True
        try:
            if self.task is not None:
                await asyncio.shield(self.task)
        finally:
            if self.listening:
                self.room.off("connection_state_changed", self.connection_handler)
                self.listening = False


def make_computer_publisher(room):
    return _ComputerPublisher(room)
