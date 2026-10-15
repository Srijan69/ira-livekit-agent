/**
 * Placeholder data for sub-project A, and nothing else.
 *
 * Weather needs the network and tasks need the Python bridge, so both are
 * faked here while the shell is built. Sub-project C deletes this file -- the
 * types it exports are the real ones, so swapping the source is a change of
 * hook, not of component.
 *
 * Kept in one obvious place so there is never a question about what in the
 * dashboard is real. Everything not in this file is.
 */

export type Weather = {
  place: string;
  temperatureC: number;
  feelsLikeC: number;
  humidity: number;
  windKph: number;
  description: string;
  /** Maps to an icon; night variants matter for the look. */
  condition: "clear" | "partly-cloudy" | "cloudy" | "rain" | "storm";
  isNight: boolean;
  /** ISO timestamp of the reading, for the stale indicator. */
  at: string;
};

export type Task = {
  id: string;
  title: string;
  /** ISO-8601. Null for a task with no date. */
  dueAt: string | null;
  done: boolean;
};

export const MOCK_WEATHER: Weather = {
  place: "Hyderabad, India",
  temperatureC: 28,
  feelsLikeC: 30,
  humidity: 64,
  windKph: 12,
  description: "Partly Cloudy",
  condition: "partly-cloudy",
  isNight: true,
  at: new Date().toISOString(),
};

/**
 * Due dates are built relative to now rather than hardcoded, so the panel
 * demonstrates "Today" and "Tomorrow" instead of showing four stale dates
 * that all read as overdue by the time anyone looks at it.
 */
const offsetHours = (hours: number): string =>
  new Date(Date.now() + hours * 3_600_000).toISOString();

export const MOCK_TASKS: Task[] = [
  { id: "t1", title: "DAA Exam Revision", dueAt: offsetHours(14), done: false },
  { id: "t2", title: "Work on Hackathon Module", dueAt: offsetHours(2), done: false },
  { id: "t3", title: "Check XAUUSD Analysis", dueAt: offsetHours(-1), done: false },
  {
    id: "t4",
    title: "College Assignment Submission",
    dueAt: offsetHours(52),
    done: false,
  },
];

/** Shown in the sidebar footer until Settings exists to hold a real name. */
export const MOCK_USER = { name: "Sai Srijan", tier: "Premium" };
