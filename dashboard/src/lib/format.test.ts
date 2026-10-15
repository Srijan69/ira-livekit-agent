import { describe, expect, it } from "vitest";

import { formatClock, formatDay, formatDue, greetingFor } from "./format";

/**
 * Dates are built from explicit local components throughout, never from ISO
 * strings with a Z suffix. That keeps the suite deterministic in any timezone
 * -- the alternative is tests that pass here and fail in CI.
 */
const at = (
  y: number,
  m: number,
  d: number,
  h = 0,
  min = 0,
): Date => new Date(y, m - 1, d, h, min, 0, 0);

describe("greetingFor", () => {
  it.each([
    ["Good Morning", at(2026, 9, 30, 6, 0)],
    ["Good Morning", at(2026, 9, 30, 11, 59)],
    ["Good Afternoon", at(2026, 9, 30, 12, 0)],
    ["Good Afternoon", at(2026, 9, 30, 16, 59)],
    ["Good Evening", at(2026, 9, 30, 17, 0)],
    // The reference image reads "Good Evening" at 07:42 PM.
    ["Good Evening", at(2026, 9, 30, 19, 42)],
    ["Good Evening", at(2026, 9, 30, 21, 59)],
    ["Good Night", at(2026, 9, 30, 22, 0)],
    ["Good Night", at(2026, 9, 30, 3, 0)],
  ])("says %s", (expected, date) => {
    expect(greetingFor(date)).toBe(expected);
  });
});

describe("formatClock", () => {
  it("splits the time from its meridiem so they can be styled apart", () => {
    expect(formatClock(at(2026, 9, 30, 19, 42))).toEqual({
      time: "07:42",
      suffix: "PM",
    });
  });

  it("pads the hour, so the digits do not shift width at 9:59", () => {
    expect(formatClock(at(2026, 9, 30, 9, 5))).toEqual({
      time: "09:05",
      suffix: "AM",
    });
  });

  it("renders midnight as 12 AM, not 00", () => {
    expect(formatClock(at(2026, 9, 30, 0, 7))).toEqual({
      time: "12:07",
      suffix: "AM",
    });
  });

  it("renders noon as 12 PM", () => {
    expect(formatClock(at(2026, 9, 30, 12, 0))).toEqual({
      time: "12:00",
      suffix: "PM",
    });
  });
});

describe("formatDay", () => {
  it("matches the reference header format", () => {
    // The reference image reads "Tue, 30 Sep 2026", but 30 September 2026 is a
    // Wednesday -- the mockup's date is internally inconsistent, as generated
    // images tend to be. We match the format, not the error: a clock that
    // disagrees with the calendar is worse than one that disagrees with a
    // mockup.
    expect(formatDay(at(2026, 9, 30))).toBe("Wed, 30 Sep 2026");
  });

  it("zero-pads a single-digit day, keeping the header width stable", () => {
    expect(formatDay(at(2026, 10, 2))).toBe("Fri, 02 Oct 2026");
  });
});

describe("formatDue", () => {
  const now = at(2026, 9, 30, 19, 42);

  it("says Today for a due time later the same day", () => {
    expect(formatDue(at(2026, 9, 30, 20, 0), now)).toEqual({
      label: "Today, 8:00 PM",
      overdue: false,
    });
  });

  it("says Tomorrow for the next calendar day", () => {
    expect(formatDue(at(2026, 10, 1, 9, 0), now)).toEqual({
      label: "Tomorrow, 9:00 AM",
      overdue: false,
    });
  });

  it("uses a short date further out", () => {
    expect(formatDue(at(2026, 10, 2, 23, 59), now)).toEqual({
      label: "2 Oct, 11:59 PM",
      overdue: false,
    });
  });

  it("marks a past time overdue but still says when", () => {
    // "Overdue" with no time is useless -- you need to know how badly.
    expect(formatDue(at(2026, 9, 30, 17, 30), now)).toEqual({
      label: "Today, 5:30 PM",
      overdue: true,
    });
  });

  it("counts Tomorrow by calendar day, not by 24 hours elapsed", () => {
    // 23:30 tonight to 00:30 tomorrow is an hour away but a day later. An
    // hours-based check would call it "Today" at half past midnight.
    expect(formatDue(at(2026, 10, 1, 0, 30), at(2026, 9, 30, 23, 30))).toEqual({
      label: "Tomorrow, 12:30 AM",
      overdue: false,
    });
  });

  it("rolls Today over at midnight rather than when the app launched", () => {
    // The panel sits open overnight. A label baked at render time would still
    // read "Tomorrow" about something now in the past.
    const tonight = at(2026, 9, 30, 21, 0);
    const task = at(2026, 10, 1, 9, 0);
    expect(formatDue(task, tonight).label).toBe("Tomorrow, 9:00 AM");
    expect(formatDue(task, at(2026, 10, 1, 1, 0)).label).toBe("Today, 9:00 AM");
  });

  it("handles a task with no due date", () => {
    expect(formatDue(null, now)).toEqual({ label: "No date", overdue: false });
  });

  it("crosses a year boundary without saying Tomorrow is in January", () => {
    expect(formatDue(at(2027, 1, 1, 9, 0), at(2026, 12, 31, 20, 0))).toEqual({
      label: "Tomorrow, 9:00 AM",
      overdue: false,
    });
  });
});
