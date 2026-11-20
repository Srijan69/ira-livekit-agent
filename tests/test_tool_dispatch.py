"""Tests for Hermes-inspired tool dispatch and argument repair."""

from tool_dispatch import format_tool_error, repair_json_string, repair_tool_arguments


def test_repair_json_string_strips_code_fences():
    text = '```json\n{"skill_name": "Open Web Page"}\n```'
    cleaned = repair_json_string(text)
    assert cleaned == '{"skill_name": "Open Web Page"}'


def test_repair_json_string_strips_trailing_commas():
    text = '{"name": "test", "num": 42,}'
    cleaned = repair_json_string(text)
    assert cleaned == '{"name": "test", "num": 42}'

    nested = '{"items": [1, 2, 3,], "val": true,}'
    cleaned_nested = repair_json_string(nested)
    assert cleaned_nested == '{"items": [1, 2, 3], "val": true}'


def test_repair_tool_arguments_coerces_skill_aliases():
    # Coerce skill_name -> name
    repaired = repair_tool_arguments(
        '{"skill_name": "Open Web Page"}',
        expected_params={"name"},
    )
    assert repaired == {"name": "Open Web Page"}


def test_repair_tool_arguments_coerces_app_and_task_aliases():
    # Coerce name -> app_name when app_name is expected
    repaired_app = repair_tool_arguments(
        {"name": "Calculator"},
        expected_params={"app_name", "context"},
    )
    assert repaired_app == {"app_name": "Calculator"}

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
    repaired = repair_tool_arguments("{'name': 'Open Web Page'}")
    assert repaired == {"name": "Open Web Page"}


def test_repair_tool_arguments_serializes_dict_inputs_json():
    repaired = repair_tool_arguments(
        {"name": "test_skill", "inputs_json": {"query": "sample"}}
    )
    assert repaired["inputs_json"] == '{"query": "sample"}'


def test_format_tool_error_provides_diagnostic_advice():
    # LookupError
    err = LookupError("skill not found")
    msg = format_tool_error(err, "view_skill")
    assert "view_skill" in msg
    assert "skill not found" in msg
    assert "try searching with alternative wording" in msg

    # TimeoutError
    time_err = TimeoutError("action timed out")
    time_msg = format_tool_error(time_err, "capture_screen")
    assert "timed out" in time_msg
    assert "inspect the screen or retry" in time_msg

    # ValueError
    val_err = ValueError("empty app name")
    val_msg = format_tool_error(val_err, "open_app")
    assert "invalid parameters" in val_msg
