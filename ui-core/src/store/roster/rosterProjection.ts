/**
 * Pure roster projection — the single conversion from a crow snapshot reply to roster state.
 *
 * Hydration and refresh both call {@link projectRosterSnapshot} after unwrapping their envelopes
 * (`{ ok, value }` for hydrate; `asQueryResult` for refresh). The projection starts after unwrap;
 * both callers pass the same {@link CrowSnapshotReply} shape.
 */

import type { CrowSessionDto, CrowSnapshotReply } from './rosterActions.js';
import type { RosterRow, RosterState } from './rosterSlice.js';

/** Project one wire session into the slice's row. Pure: single DTO→domain mapping point. */
export function toRosterRow(session: CrowSessionDto): RosterRow {
  return {
    agentId: session.agent_id,
    role: session.role,
    ticketId: session.ticket_id ?? null,
    ticketTitle: session.ticket_title ?? null,
    harness: session.harness ?? null,
    model: session.model ?? null,
    status: session.status,
    session: session.display_name ?? null,
    ...(session.session_id == null ? {} : { sessionId: session.session_id }),
    worktreePath: session.worktree_path ?? null,
    lastSeen: session.last_seen ?? null,
    openEscalations: session.open_escalations ?? 0,
    maxSeverity: session.max_severity ?? 0,
  };
}

/**
 * Convert one crow snapshot reply into roster application state.
 * Always writes `ready` with a cleared error — callers that need a loading lifecycle apply that
 * themselves before invoking the shared refresh drain.
 */
export function projectRosterSnapshot(reply: CrowSnapshotReply): { roster: RosterState } {
  return {
    roster: {
      rows: reply.sessions.map(toRosterRow),
      status: 'ready',
      error: null,
    },
  };
}
