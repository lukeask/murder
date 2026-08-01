/**
 * Shared formatters for the doc-style resource lists (Plans / Notes / Reports).
 *
 * The three doc panels render the same two-line entry over the same two domain fields (a char count
 * and an `updated_at` timestamp), so the formatting belongs in ONE place. Keeping these here stops
 * the drift that crept in before: notes/reports had copied an older version that showed a raw ISO
 * date (`YYYY-MM-DD HH:MM`) and a `padEnd`'d char count, while plans had moved on to the compact
 * `Mon. dd HH:MM` form with an unpadded count. These are the canonical (plans) versions; the three
 * selectors import them so a future change lands on all three at once.
 */

/** Three-letter month abbreviations, indexed by 0-based month, for {@link formatUpdatedAt}. */
const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

/**
 * Format an ISO-8601 datetime to `Mon. dd HH:MM` (e.g. `Jun. 10 09:32`). Parsed by slicing the fixed
 * ISO layout `YYYY-MM-DDTHH:MM:SS` — no Date object, so it's timezone-stable (the stored time is shown
 * verbatim) and matches the rest of the selectors' string-slice formatting.
 */
export function formatUpdatedAt(iso: string): string {
  const month = MONTHS[Number(iso.slice(5, 7)) - 1] ?? '???';
  const day = iso.slice(8, 10);
  const time = iso.slice(11, 16);
  return `${month}. ${day} ${time}`;
}

/**
 * Format a character count as a compact, human-readable display string (e.g. `3,998 chars`). No
 * padding — the `· updated` column follows the count directly with a single separator, so a short
 * count doesn't carry a run of trailing spaces before the `·`.
 */
export function formatCharCount(n: number): string {
  return `${n.toLocaleString()} chars`;
}
