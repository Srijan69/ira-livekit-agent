# IRA evaluation repairs

Repair three backend defects demonstrated by typed goal evaluation and offline regression tests.

1. Restore the existing semantic action/completion judge and restricted provider-outage fallback. A file request must not automatically admit an unrelated playlist action. Preserve request identity, cancellation, replay rejection, and bounded policy timeouts.
2. Select an exact registered Windows application, with a small fixed alias table, before issuing a launch. Missing or ambiguous names must not fall back to Start-menu or web searches. A launch request is uncertain until independently observed, and cannot be blindly repeated.
3. Resolve supported relative deadlines from the trusted human request and local clock before task persistence. On 9 November 2026 in UTC+05:30, tomorrow at 16:30 must persist as 10 November 11:00 UTC. Ambiguous relative requests must not write a row.

Keep the TaskStore UTC schema and dashboard telemetry contract unchanged. Tests must simulate native/provider boundaries and use temporary stores; no desktop control, microphone changes, or live IRA sessions are needed to verify this batch.

Relative deadlines deliberately support today, tomorrow, and day after tomorrow with one AM/PM or two-digit HH:MM time, using local time or explicit UTC/GMT/IST. Other dates retain the existing explicitly zoned ISO contract. Unsupported and ambiguous forms need clarification.

The implementation is not a proof of semantic safety or actual app launch. Live typed retests remain required. JSON output refusal, unexplained generic error responses, and unfinished file/browser evaluations are outside this batch.
