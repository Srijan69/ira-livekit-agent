/**
 * Date and time formatting for the header and the task list.
 *
 * Formatted from explicit arrays rather than `Intl.DateTimeFormat` on purpose.
 * This is a dashboard with a fixed visual spec, not an internationalised app,
 * and ICU drifts: recent versions render September as "Sept" under en-GB,
 * which would silently widen the header. Fixed tables can't drift.
 */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

const pad = (n: number): string => String(n).padStart(2, "0");

/** Local midnight, used to compare calendar days rather than elapsed hours. */
const startOfDay = (date: Date): number =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

const meridiem = (date: Date): "AM" | "PM" => (date.getHours() < 12 ? "AM" : "PM");

/** 12-hour hour, with midnight and noon both reading as 12. */
const hour12 = (date: Date): number => date.getHours() % 12 || 12;

/** "Good Evening" -- the greeting beside IRA's name in the header. */
export function greetingFor(date: Date): string {
  const h = date.getHours();
  if (h >= 22 || h < 5) return "Good Night";
  if (h < 12) return "Good Morning";
  if (h < 17) return "Good Afternoon";
  return "Good Evening";
}

/**
 * The header clock, split so the meridiem can be styled smaller than the time.
 * The hour is zero-padded so the digits don't shift width between 9:59 and
 * 10:00 -- paired with `tabular-nums`, the clock never reflows.
 */
export function formatClock(date: Date): { time: string; suffix: "AM" | "PM" } {
  return {
    time: `${pad(hour12(date))}:${pad(date.getMinutes())}`,
    suffix: meridiem(date),
  };
}

/** "Tue, 30 Sep 2026" */
export function formatDay(date: Date): string {
  return `${WEEKDAYS[date.getDay()]}, ${pad(date.getDate())} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

/** "8:00 PM" -- unpadded, unlike the clock, matching the task rows. */
function formatTimeOfDay(date: Date): string {
  return `${hour12(date)}:${pad(date.getMinutes())} ${meridiem(date)}`;
}

/**
 * A task's due label, relative to now.
 *
 * `now` is a parameter rather than `Date.now()` so the caller can re-render on
 * a clock tick. That is what keeps a dashboard left open overnight from still
 * describing this morning as "Tomorrow".
 */
export function formatDue(
  due: Date | null,
  now: Date,
): { label: string; overdue: boolean } {
  if (!due) return { label: "No date", overdue: false };

  const overdue = due.getTime() < now.getTime();
  const time = formatTimeOfDay(due);

  // Rounded, because a DST boundary makes a day 23 or 25 hours long and a
  // truncating division would land a day off twice a year.
  const days = Math.round((startOfDay(due) - startOfDay(now)) / 86_400_000);

  if (days === 0) return { label: `Today, ${time}`, overdue };
  if (days === 1) return { label: `Tomorrow, ${time}`, overdue };
  return {
    label: `${due.getDate()} ${MONTHS[due.getMonth()]}, ${time}`,
    overdue,
  };
}
