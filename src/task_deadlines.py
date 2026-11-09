"""Resolve only unambiguous relative deadlines from the trusted human request."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone

_RELATIVE_DATE = re.compile(r"\b(day after tomorrow|tomorrow|today)\b", re.I)
_TIME_12 = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b", re.I)
_TIME_24 = re.compile(r"\bat\s+(\d{2}):(\d{2})\b", re.I)
_UNSUPPORTED_ZONE = re.compile(
    r"\b(?:EST|EDT|CST|CDT|MST|MDT|PST|PDT|BST|CET|CEST|JST|AEST|AEDT)\b"
    r"|\b(?:UTC|GMT)\s*[+-]"
    r"|\b(?:Africa|America|Asia|Atlantic|Australia|Europe|Pacific)/[\w/+-]+",
    re.I,
)


def resolve_task_deadline(
    request_text: str, proposed_due_at: str | None, now: datetime
) -> str | None:
    """Use the local calendar/time, not a model's guessed UTC conversion.

    Other dated requests retain the existing explicitly zoned ISO contract.
    A relative date requires exactly one supported, unambiguous clock time.
    """
    dates = list(_RELATIVE_DATE.finditer(request_text))
    if not dates:
        return proposed_due_at
    if len(dates) != 1:
        raise ValueError("Please clarify one deadline date")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A local timezone is required to resolve the deadline")
    if _UNSUPPORTED_ZONE.search(request_text):
        raise ValueError(
            "Please clarify the deadline timezone with an explicit date and offset"
        )

    zones = set(re.findall(r"\b(UTC|GMT|IST)\b", request_text, flags=re.I))
    zones = {zone.upper() for zone in zones}
    if "IST" in zones and zones & {"UTC", "GMT"}:
        raise ValueError("Please clarify one deadline timezone")
    zone = (
        timezone(timedelta(hours=5, minutes=30))
        if "IST" in zones
        else timezone.utc
        if zones & {"UTC", "GMT"}
        else now.tzinfo
    )

    twelve = list(_TIME_12.finditer(request_text))
    twenty_four = [
        match
        for match in _TIME_24.finditer(request_text)
        if not any(
            other.start() <= match.start(1) and match.end(2) <= other.end()
            for other in twelve
        )
    ]
    if len(twelve) + len(twenty_four) != 1:
        raise ValueError("Please clarify one deadline time, including AM/PM or HH:MM")
    selected_times = twelve + twenty_four
    if any(
        not any(
            selected.start() <= clock.start() and clock.end() <= selected.end()
            for selected in selected_times
        )
        for clock in re.finditer(r"\b\d{1,2}:\d{2}\b", request_text)
    ):
        raise ValueError("Please clarify one deadline time, including AM/PM or HH:MM")
    if twelve:
        match = twelve[0]
        hour, minute = int(match[1]), int(match[2] or 0)
        if not 1 <= hour <= 12:
            raise ValueError("Deadline hour must be between 1 and 12 with AM/PM")
        hour = hour % 12 + (12 if match[3].upper() == "PM" else 0)
    else:
        hour, minute = map(int, twenty_four[0].groups())
    clock = time(hour, minute)
    days = {"today": 0, "tomorrow": 1, "day after tomorrow": 2}[dates[0][1].lower()]
    day = now.astimezone(zone).date() + timedelta(days=days)
    deadline = datetime.combine(day, clock, tzinfo=zone)
    if deadline.replace(fold=0).utcoffset() != deadline.replace(fold=1).utcoffset():
        raise ValueError("Please clarify the deadline offset across the clock change")
    if deadline.astimezone(timezone.utc).astimezone(zone) != deadline:
        raise ValueError("The deadline time does not exist across the clock change")
    return deadline.isoformat(timespec="seconds")
