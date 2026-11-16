/**
 * The moment every unit test runs at: Sunday 15 November 2026, 12:00 UTC. src/test-setup.ts freezes
 * `Date` here (and the tests run in UTC), so texts such as "3 hours ago" and the demo's data, which
 * is relative to the current time, are the same whatever the day, the hour and the time zone.
 */
export const NOW = Date.UTC(2026, 10, 15, 12)
