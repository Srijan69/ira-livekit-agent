"""Tests for Hermes-inspired tool dispatch and argument repair."""

from tool_dispatch import format_tool_error, repair_json_string, repair_tool_arguments


def test_repair_json_string_strips_code_fences():
    text = '```json\n{"playlist_name": "My Phonk Songs"}\n```'
    cleaned = repair_json_string(text)
    assert cleaned == '{"playlist_name": "My Phonk Songs"}'


def test_repair_json_string_strips_trailing_commas():
    text = '{"name": "test", "num": 42,}'
    cleaned = repair_json_string(text)
    assert cleaned == '{"name": "test", "num": 42}'

    nested = '{"items": [1, 2, 3,], "val": true,}'
    cleaned_nested = repair_json_string(nested)
    assert cleaned_nested == '{"items": [1, 2, 3], "val": true}'


def test_repair_tool_arguments_coerces_spotify_aliases():
    # Coerce playlist_name -> name
    repaired = repair_tool_arguments(
        '{"playlist_name": "My Phonk Songs"}',
        expected_params={"name"},
    )
    assert repaired == {"name": "My Phonk Songs"}

    # Coerce song_name -> name, artist_name -> artist
    repaired_track = repair_tool_arguments(
        {"song_name": "Metamorphosis", "singer": "Interworld"},
        expected_params={"name", "artist"},
    )
    assert repaired_track == {"name": "Metamorphosis", "artist": "Interworld"}


def test_repair_tool_arguments_coerces_app_and_task_aliases():
    # Coerce name -> app_name when app_name is expected
    repaired_app = repair_tool_arguments(
        {"name": "Spotify"},
        expected_params={"app_name", "context"},
    )
    assert repaired_app == {"app_name": "Spotify"}

    # Coerce task -> title when title is expected
    repaired_task = repair_tool_arguments(
        {"task": "Buy groceries", "due_date": "2026-10-20T10:00:00Z"},
        expected_params={"title", "due_at", "context"},
    )
    assert repaired_task == {"title": "Buy groceries", "due_at": "2026-10-20T10:00:00Z"}

    # Coerce id -> task_id when task_id is expected
    repaired_complete = repair_tool_arguments(
        {"id": 5},
        expected_params={"task_id", "context"},
    )
    assert repaired_complete == {"task_id": 5}


def test_repair_tool_arguments_handles_single_quotes():
    repaired = repair_tool_arguments("{'name': 'My Phonk Songs'}")
    assert repaired == {"name": "My Phonk Songs"}


def test_repair_tool_arguments_serializes_dict_inputs_json():
    repaired = repair_tool_arguments(
        {"name": "test_skill", "inputs_json": {"query": "sample"}}
    )
    assert repaired["inputs_json"] == '{"query": "sample"}'


def test_format_tool_error_provides_diagnostic_advice():
    # LookupError
    err = LookupError("playlist not found")
    msg = format_tool_error(err, "spotify_play_playlist")
    assert "spotify_play_playlist" in msg
    assert "playlist not found" in msg
    assert "try searching with alternative wording" in msg

    # TimeoutError
    time_err = TimeoutError("action timed out")
    time_msg = format_tool_error(time_err, "capture_screen")
    assert "timed out" in time_msg
    assert "inspect the screen or retry" in time_msg

    # ValueError
    val_err = ValueError("empty track name")
    val_msg = format_tool_error(val_err, "spotify_play_track")
    assert "invalid parameters" in val_msg
