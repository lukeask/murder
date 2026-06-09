/**
 * Crow-health classification — the Ink port of Textual's `murder/app/tui/crow_health.py:34-66`.
 *
 * Health is a *client-side* fact: the border/edge colour of one crow, derived from its agent status,
 * any open escalation linked to its ticket, and a 60-second stuck-heartbeat rule. The classifier is
 * intentionally pure (no React, no store, no clock-by-default) so every branch is unit-testable in
 * isolation — the same property the Python module guards.
 *
 * ── DATA-SHAPE GAP (flag for the service / B13) ───────────────────────────────────────────────────
 * The live Ink crow snapshot (`CrowSessionDto` in `store/roster/rosterActions.ts`, projected to
 * `RosterRow`) carries ONLY `status`. The Python `CrowSessionSummary` additionally carries
 * `open_escalations`, `max_severity`, and `last_seen`, which feed the escalation-RED and stuck-YELLOW
 * branches. Until the service adds those fields to `state.crow_snapshot`, the selector passes the
 * defaults (`openEscalations: 0`, `maxSeverity: 0`, `stuck: false`), so in practice only the
 * status-driven branches (RED for failed/dead/blocked/escalating, GREEN for running/idle, NEUTRAL
 * otherwise) are exercised on live data. The escalation/stuck branches are fully implemented and
 * tested here; they light up automatically the instant the wire grows the fields and the projection
 * forwards them. DO NOT invent the fields on the wire — that is the service's call (mirrors the
 * "NOTE FOR THE SERVICE" convention in `rosterActions.ts`).
 */

/** The four health states a crow's border edge can take. Mirrors Python `Health`. */
export type Health = 'green' | 'yellow' | 'red' | 'neutral';

/** Agent statuses that turn a crow's edge RED even with no open escalation. Mirrors `_RED_STATUSES`. */
const RED_STATUSES: ReadonlySet<string> = new Set(['escalating', 'blocked', 'failed', 'dead']);

/** Agent statuses that read as healthy/live. Mirrors `_GREEN_STATUSES`. */
const GREEN_STATUSES: ReadonlySet<string> = new Set(['running', 'idle']);

/**
 * A live crow with no heartbeat newer than this is "stuck-but-alive" (YELLOW). 60s, matching
 * Python `STUCK_AFTER`. Kept in milliseconds because JS time arithmetic is ms-native.
 */
export const STUCK_AFTER_MS = 60_000;

/**
 * Severity ≥ this is "needs human attention" and turns a crow RED even when the open-row count reads
 * zero (defensive — count and max-severity should align in practice). Mirrors `_RED_SEVERITY_THRESHOLD`.
 */
const RED_SEVERITY_THRESHOLD = 2;

/** Inputs to {@link classifyCrowHealth}. All optional but `status`; defaults match the Python kwargs. */
export interface CrowHealthInputs {
  /** Agent status string (`'running' | 'idle' | 'failed' | …`), or `null`/absent. */
  readonly status: string | null;
  /** Count of open escalations linked to this crow's ticket. Defaults to 0. */
  readonly openEscalations?: number;
  /** Max severity across this crow's open escalations. Defaults to 0. */
  readonly maxSeverity?: number;
  /** Caller-decided stuck-but-alive flag (heartbeat older than {@link STUCK_AFTER_MS}). Defaults false. */
  readonly stuck?: boolean;
}

/**
 * Pick the health/border colour for one crow — the testable core. Pure: same input → same output.
 *
 * Precedence (first match wins), faithful to Python `classify`:
 *   RED     — an open escalation linked to this crow's ticket (`openEscalations > 0`), OR a
 *             severity ≥ {@link RED_SEVERITY_THRESHOLD}, OR the agent is in a red status.
 *   YELLOW  — heartbeat says stuck-but-alive (caller passes the `stuck` flag).
 *   GREEN   — agent is running or idle.
 *   NEUTRAL — done, or any state we have no positive read on.
 */
export function classifyCrowHealth({
  status,
  openEscalations = 0,
  maxSeverity = 0,
  stuck = false,
}: CrowHealthInputs): Health {
  if (openEscalations > 0 || maxSeverity >= RED_SEVERITY_THRESHOLD) {
    return 'red';
  }
  const norm = (status ?? '').toLowerCase();
  if (RED_STATUSES.has(norm)) {
    return 'red';
  }
  if (stuck) {
    return 'yellow';
  }
  if (GREEN_STATUSES.has(norm)) {
    return 'green';
  }
  return 'neutral';
}

/**
 * Is a *live* crow's heartbeat older than {@link STUCK_AFTER_MS}? Mirrors Python `is_stuck`: only
 * running/idle crows can be "stuck" (a failed/done crow's silence is not stuck-but-alive), and a
 * missing `lastSeenMs` is treated as not-stuck (we have no positive read).
 *
 * Times are epoch-milliseconds (the wire/JS-native form) — the caller converts a wire timestamp once.
 */
export function isStuck({
  status,
  lastSeenMs,
  nowMs,
}: {
  readonly status: string | null;
  readonly lastSeenMs: number | null;
  readonly nowMs: number;
}): boolean {
  const norm = (status ?? '').toLowerCase();
  if (!GREEN_STATUSES.has(norm)) {
    return false;
  }
  if (lastSeenMs === null) {
    return false;
  }
  return nowMs - lastSeenMs > STUCK_AFTER_MS;
}

/**
 * Health → Ink colour-name map for a crow's left-edge marker. Textual's `$crow-health-*` theme vars
 * have no Ink equivalent, so this maps to literal Ink colour names. `neutral` reads as `gray` (no
 * positive signal), matching the dimmed Textual neutral edge.
 */
export const HEALTH_EDGE_COLOR: Readonly<Record<Health, string>> = {
  red: 'red',
  yellow: 'yellow',
  green: 'green',
  neutral: 'gray',
};
