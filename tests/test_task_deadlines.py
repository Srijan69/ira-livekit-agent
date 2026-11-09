"""Literal relative-date expectations, independent of the model's timestamp."""

from datetime import datetime

import pytest

from task_deadlines import resolve_task_deadline

NOW = datetime.fromisoformat("2026-11-09T18:00:00+05:30")


@pytest.mark.parametrize("proposal", [None, "2026-11-10T16:30:00Z"])
def test_relative_local_deadline_overrides_missing_or_wrong_utc_proposal(proposal):
    assert (
        resolve_task_deadline(
            "Add Review the evaluation report tomorrow at 4:30 PM", proposal, NOW
        )
        == "2026-11-10T16:30:00+05:30"
    )


@pytest.mark.parametrize(
    "goal,expected",
    [
        ("Add task today at 12 AM", "2026-11-09T00:00:00+05:30"),
        ("Add task tomorrow at 12 PM", "2026-11-10T12:00:00+05:30"),
        ("Add task tomorrow at 16:30", "2026-11-10T16:30:00+05:30"),
        ("Add task day after tomorrow at 4:30 PM", "2026-11-11T16:30:00+05:30"),
        ("Add task tomorrow at 4:30 PM UTC", "2026-11-10T16:30:00+00:00"),
        ("Add task tomorrow at 4:30 PM IST", "2026-11-10T16:30:00+05:30"),
    ],
)
def test_supported_times_and_explicit_timezone_are_resolved(goal, expected):
    assert resolve_task_deadline(goal, None, NOW) == expected


def test_relative_day_uses_the_requested_timezone_calendar():
    now = datetime.fromisoformat("2026-11-09T23:30:00-05:00")
    assert resolve_task_deadline("Add task tomorrow at 00:15 UTC", None, now) == (
        "2026-11-11T00:15:00+00:00"
    )


def test_local_date_rollover_is_calendar_based():
    now = datetime.fromisoformat("2026-12-31T23:59:00+05:30")
    assert resolve_task_deadline("Add task tomorrow at 8 AM", None, now) == (
        "2027-01-01T08:00:00+05:30"
    )


@pytest.mark.parametrize(
    "goal",
    [
        "Add task tomorrow at 4",
        "Add task tomorrow at 4 PM or 5 PM",
        "Add task today or tomorrow at 4 PM",
        "Add task tomorrow at 13 PM",
        "Add task tomorrow at 4:99 PM",
        "Add task tomorrow at 4 PM PST",
        "Add task tomorrow at 4 PM UTC+05:30",
        "Add task tomorrow at 4:30",
        "Add task tomorrow at 16:30 or 17:00",
        "Add task tomorrow at 4 PM or 4:30",
    ],
)
def test_ambiguous_relative_dates_never_accept_a_model_guess(goal):
    with pytest.raises(ValueError):
        resolve_task_deadline(goal, "2026-11-10T16:30:00Z", NOW)


def test_no_relative_deadline_preserves_explicit_iso_contract():
    assert (
        resolve_task_deadline("Add report review", "2026-11-10T11:00:00Z", NOW)
        == "2026-11-10T11:00:00Z"
    )
    assert resolve_task_deadline("Add report review", None, NOW) is None


def test_timezone_naive_clock_cannot_resolve_a_relative_deadline():
    with pytest.raises(ValueError, match="timezone"):
        resolve_task_deadline("Add task tomorrow at 4 PM", None, datetime(2026, 11, 9))
