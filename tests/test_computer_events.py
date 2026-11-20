"""Bounded screenshot evidence travels separately from tiny lifecycle packets."""

import asyncio
import base64
import io
import json
from types import SimpleNamespace

from PIL import Image

from computer_events import (
    COMPUTER_OBSERVATION_TOPIC,
    MAX_COMPUTER_EVENT_BYTES,
    build_computer_event,
    make_computer_publisher,
)


def description():
    image = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(image, "JPEG")
    return {
        "observation_id": "observation-1",
        "captured_at": "2026-11-07T08:00:00Z",
        "image_size": [120, 80],
        "image_bytes": image.getvalue(),
        "image_mime_type": "image/jpeg",
        "targets": [
            {
                "id": "t1",
                "name": "Save",
                "role": "button",
                "image_bounds": [-5, 10, 25, 20],
            }
        ],
        "windows": [],
        "cursor_image_position": [30, 20],
        "text": "must not travel",
    }


def event(phase="observed", **kwargs):
    return build_computer_event(
        description(),
        run_id="run-1",
        call_id="call-1",
        tool_name="capture_screen",
        sequence=1,
        phase=phase,
        **kwargs,
    )


def test_snapshot_preserves_image_coordinates_and_allowlisted_fields():
    output = event()
    assert output["screenshot_data_url"].startswith("data:image/jpeg;base64,")
    assert Image.open(
        io.BytesIO(base64.b64decode(output["screenshot_data_url"].split(",")[1]))
    ).size == (120, 80)
    assert output["targets"][0]["image_bounds"] == [0, 10, 20, 20]
    assert "must not travel" not in json.dumps(output)
    assert len(json.dumps(output).encode()) <= MAX_COMPUTER_EVENT_BYTES


def test_action_metadata_contains_no_text_or_arbitrary_status_copy():
    output = event(
        "before",
        action={
            "action": "type",
            "observation_id": "observation-1",
            "image_points": [],
            "text": "Private text",
            "label": "Claims success",
            "args": {"secret": "value"},
        },
    )
    assert output["action"] == {
        "action": "type",
        "observation_id": "observation-1",
        "image_points": [],
    }
    assert "Private text" not in json.dumps(output)


def test_status_event_needs_no_screen_and_drops_old_frame():
    output = event("cleared")
    assert output["phase"] == "cleared"
    assert "screenshot_data_url" not in output
    assert "targets" not in output


def test_invalid_image_or_geometry_cannot_publish_actionable_preview():
    invalid = description()
    invalid["image_size"] = [0, 80]
    assert (
        build_computer_event(
            invalid,
            run_id="r",
            call_id="c",
            tool_name="capture_screen",
            sequence=1,
            phase="observed",
        )
        is None
    )
    invalid = description()
    invalid["image_bytes"] = b"not an image"
    assert (
        build_computer_event(
            invalid,
            run_id="r",
            call_id="c",
            tool_name="capture_screen",
            sequence=1,
            phase="observed",
        )
        is None
    )


async def test_disconnected_snapshots_are_never_backlogged_for_reconnect():
    sent = []

    async def send_text(payload, **kwargs):
        sent.append((json.loads(payload), kwargs))

    room = SimpleNamespace(
        connected=False, local_participant=SimpleNamespace(send_text=send_text)
    )
    room.isconnected = lambda: room.connected
    publisher = make_computer_publisher(room)
    publisher(event())
    room.connected = True
    await asyncio.sleep(0)
    assert sent == []
    await publisher.aclose()


async def test_stream_sends_remain_ordered_and_lifecycle_errors_do_not_escape():
    sent = []

    async def send_text(payload, **kwargs):
        await asyncio.sleep(0)
        sent.append((json.loads(payload)["sequence"], kwargs["topic"]))

    room = SimpleNamespace(
        isconnected=lambda: True, local_participant=SimpleNamespace(send_text=send_text)
    )
    publisher = make_computer_publisher(room)
    first = event("before")
    second = {**event("after"), "sequence": 2}
    publisher(first)
    publisher(second)
    await publisher.aclose()
    assert sent == [(1, COMPUTER_OBSERVATION_TOPIC), (2, COMPUTER_OBSERVATION_TOPIC)]


async def test_oversized_event_and_failed_send_are_nonfatal():
    sent = []

    async def send_text(payload, **kwargs):
        sent.append(payload)
        raise RuntimeError("room ended")

    room = SimpleNamespace(
        isconnected=lambda: True, local_participant=SimpleNamespace(send_text=send_text)
    )
    publisher = make_computer_publisher(room)
    publisher({**event(), "extra": "x" * MAX_COMPUTER_EVENT_BYTES})
    publisher(event())
    await publisher.aclose()
    assert len(sent) == 1


async def test_room_event_wrapper_closes_computer_streams():
    from agent import _room_event_publisher

    sent = []

    async def send_text(payload, **kwargs):
        sent.append(json.loads(payload)["sequence"])

    room = SimpleNamespace(
        isconnected=lambda: True, local_participant=SimpleNamespace(send_text=send_text)
    )
    publisher = _room_event_publisher(room)
    publisher(event())
    await publisher.aclose()
    publisher({**event(), "sequence": 2})
    await asyncio.sleep(0)
    assert sent == [1]


async def test_failed_stream_cannot_replay_queued_screens_after_reconnect():
    sent = []
    room = SimpleNamespace(connected=True)
    room.isconnected = lambda: room.connected

    async def send_text(payload, **kwargs):
        sequence = json.loads(payload)["sequence"]
        sent.append(sequence)
        if sequence == 1:
            room.connected = False
            await asyncio.sleep(0)
            room.connected = True
            raise RuntimeError("stream disconnected")

    room.local_participant = SimpleNamespace(send_text=send_text)
    publisher = make_computer_publisher(room)
    publisher(event())
    publisher({**event(), "sequence": 2})
    await publisher.aclose()
    assert sent == [1]


async def test_connection_change_aborts_old_stream_and_accepts_only_fresh_frames():
    sent = []
    listeners = {}
    entered, released = asyncio.Event(), asyncio.Event()

    class Room:
        connected = True

        def isconnected(self):
            return self.connected

        def on(self, name, callback):
            listeners[name] = callback

        def off(self, name, callback):
            assert listeners.pop(name) is callback

    async def send_text(payload, **kwargs):
        sequence = json.loads(payload)["sequence"]
        if sequence == 1:
            entered.set()
            await released.wait()
        sent.append(sequence)

    room = Room()
    room.local_participant = SimpleNamespace(send_text=send_text)
    publisher = make_computer_publisher(room)
    publisher(event())
    publisher({**event(), "sequence": 2})
    await entered.wait()
    room.connected = False
    listeners["connection_state_changed"]("disconnected")
    room.connected = True
    listeners["connection_state_changed"]("connected")
    publisher({**event(), "sequence": 3})
    released.set()
    await publisher.aclose()
    assert sent == [3]
    assert listeners == {}
