/**
 * Agent identity — the discriminated-union type that routes chat to the correct agent.
 *
 * This is the explicit anti-pattern **replacement** for conversation-id string-prefix parsing.
 * The old TUI used `conversation_id_for_agent_prefix(prefix)` where `prefix` was derived from
 * the session name — stringly-typed, fragile, named as such in the architecture doc.
 *
 * Here: `AgentIdentity` is a tagged union derived from `RosterRow.role` + `RosterRow.ticketId`
 * (the same fields C9 uses for grouping in `crowsSelectors.ts`). The type carries enough
 * information to display the agent in the UI AND to route the `agent.message` action to
 * `row.agentId` — no string parsing, no conversation_id, no reverse map.
 *
 * Rule 1 / anti-pattern note: `agentId` is carried in every variant because `agent.message`
 * always needs it, and it's already on the `RosterRow` — no lookup needed. The other fields
 * (plan, ticketId) are display context only; routing is always `agentId`.
 *
 * Rule 2: derivation lives here (selectors), not in components.
 */

import type { RosterRow } from '../store/roster/rosterSlice.js';

// ---------------------------------------------------------------------------
// Session-name label helper (item 11 — strip the `murder_<repo>_<role…>_` prefix)
// ---------------------------------------------------------------------------

/**
 * Strip the `murder_<repo>_<role…>_` prefix from a tmux session name so the UI shows only the
 * agent's own name. The single home for this transform — used by both {@link deriveAgentIdentity}
 * (the chat target label) and `crowsSelectors.toRowView` (the Crows-pane row name), so the two
 * never drift.
 *
 * The grammar comes from `murder/runtime/terminal/session_names.py` —
 * `session_name_template = "murder_{project}_{role}{suffix}"` — and the real `role`/`suffix`
 * shapes the orchestrator/runner produce:
 *   - planner            → `murder_<repo>_planner_<plan>`                    → `<plan>`
 *   - planning_handler   → `murder_<repo>_planning_handler_<plan>`           → `<plan>`
 *   - ticket crow        → `murder_<repo>_crow_<ticketId>`                   → `<ticketId>`
 *   - crow_handler       → `murder_<repo>_crow_handler_<ticketId>`          → `<ticketId>`
 *   - rogue crow         → `murder_<repo>_crow_<harness>_rogue_<name>`       → `<name>`
 *   - collaborator       → `murder_<repo>_collaborator`                      → (no suffix; unchanged)
 *   - notetaker          → `murder_<repo>_notetaker_<x>`                     → `<x>`
 *
 * The `<repo>` segment is matched non-greedily so a single-token project name peels off before the
 * role token; rogue is checked FIRST (its `crow_<harness>_rogue_` form must not be mistaken for the
 * plain `crow_` ticket prefix). When nothing matches (a non-conforming or already-bare name) the raw
 * session is returned unchanged — fall-through, never a throw.
 */
export function stripSessionPrefix(session: string): string {
  // Rogue: `murder_<repo>_crow_<harness>_rogue_<name>` → `<name>`. Matched first so the embedded
  // `crow_` doesn't get peeled as a plain ticket-crow prefix below.
  const rogue = /^murder_.+?_crow_[^_]+_rogue_(.+)$/.exec(session);
  if (rogue?.[1]) {
    return rogue[1];
  }
  // Every other role: `murder_<repo>_<role>_<name>` → `<name>`. The role tokens are listed
  // longest-first (`planning_handler` before `planner`, `crow_handler` before `crow`) so the
  // alternation never half-matches a longer role.
  const role =
    /^murder_.+?_(?:planning_handler|planner|crow_handler|crow|collaborator|notetaker)_(.+)$/.exec(
      session,
    );
  if (role?.[1]) {
    return role[1];
  }
  // No suffix (e.g. a bare `murder_<repo>_collaborator`) or a non-conforming name: leave it as-is.
  return session;
}

/**
 * Whether a `crow` row is assigned to a ticket — the rogue-vs-ticket discriminant (item 9a).
 *
 * The wire `ticket_id` for a rogue crow is an **empty string**, not null: the orchestrator registers
 * a rogue with `ticket_id=''`, the read-model's `_optional_str('')` returns `''` (not `None`), and
 * the slice's `session.ticket_id ?? null` keeps `''` (empty string is not nullish). So the old
 * `ticketId === null` test mis-classified every rogue as a ticket crow, which is why rogues never
 * landed in the Rogue Crows group. Treat null AND empty/whitespace as "no ticket" so a rogue is a
 * rogue regardless of which sentinel the backend sends.
 */
export function hasTicket(ticketId: string | null): ticketId is string {
  return ticketId !== null && ticketId.trim() !== '';
}

// ---------------------------------------------------------------------------
// The discriminated union
// ---------------------------------------------------------------------------

/** The collaborator — always exactly one; favorited by default. */
export interface CollaboratorIdentity {
  readonly kind: 'collaborator';
  readonly agentId: string;
  readonly label: string;
}

/** A user-facing planning agent (role='planner'). */
export interface PlannerIdentity {
  readonly kind: 'planner';
  readonly agentId: string;
  readonly label: string;
  /**
   * The planner's session name, used as a display label. In the plan spec this is `plan`
   * (a plan name); the wire delivers it as the session name on the `RosterRow`. Named `plan`
   * here to match the spec's union shape `{kind:'planner',plan}`.
   */
  readonly plan: string;
}

/** A rogue crow (role='crow', ticketId===null); favorited on creation per the spec. */
export interface RogueIdentity {
  readonly kind: 'rogue';
  readonly agentId: string;
  readonly label: string;
  /** The rogue's agent id, used as a display label when no session name is available. */
  readonly id: string;
}

/** A ticket crow (role='crow', ticketId!==null). */
export interface TicketIdentity {
  readonly kind: 'ticket';
  readonly agentId: string;
  readonly label: string;
  /** The assigned ticket id. Named `id` to match the plan's union shape `{kind:'ticket',id}`. */
  readonly id: string;
}

/**
 * The discriminated-union agent identity. Derived from `RosterRow.role` + `RosterRow.ticketId`.
 * NEVER derived from conversation_id string parsing (the named anti-pattern in the architecture).
 *
 * Shape matches the plan: `{kind:'collaborator'} | {kind:'planner',plan} | {kind:'rogue',id} | {kind:'ticket',id}`
 * plus `agentId` in every variant (required for `agent.message` routing).
 *
 * Route `agent.message` using `identity.agentId` — always available, never a lookup.
 */
export type AgentIdentity = CollaboratorIdentity | PlannerIdentity | RogueIdentity | TicketIdentity;

// ---------------------------------------------------------------------------
// Derivation (rule 2: lives here, not in components)
// ---------------------------------------------------------------------------

/**
 * Derive the `AgentIdentity` for a `RosterRow`. Returns `null` for infrastructure/handler roles
 * (the same set excluded by `crowsSelectors.rowToGroup`): `'planning_handler'`, `'crow_handler'`,
 * `'notetaker'`, and any unknown role.
 *
 * NO string parsing: the only inputs are `row.role` (a closed union narrowed by switch) and
 * `row.ticketId` (a typed nullable field). This is the whole anti-pattern replacement.
 *
 * CONTRACT: 1:1 agent:conversation (same assumption as `crowsSelectors.ts` rogue/ticket split).
 * If the service assigns a ticket_id to a rogue crow, its identity becomes 'ticket', not 'rogue'.
 */
export function deriveAgentIdentity(row: RosterRow): AgentIdentity | null {
  // The session name carries the `murder_<repo>_<role…>_` prefix; the label shows only the agent's
  // own name (item 11). One shared helper so this matches the Crows-pane row name exactly.
  const sessionLabel = row.session !== null ? stripSessionPrefix(row.session) : row.agentId;
  switch (row.role) {
    case 'collaborator':
      return {
        kind: 'collaborator',
        agentId: row.agentId,
        label: sessionLabel,
      };
    case 'planner':
      return {
        kind: 'planner',
        agentId: row.agentId,
        label: sessionLabel,
        plan: sessionLabel,
      };
    case 'crow':
      if (!hasTicket(row.ticketId)) {
        return {
          kind: 'rogue',
          agentId: row.agentId,
          label: sessionLabel,
          id: row.agentId,
        };
      }
      return {
        kind: 'ticket',
        agentId: row.agentId,
        label: row.ticketTitle ?? row.ticketId,
        id: row.ticketId,
      };
    default:
      // 'planning_handler' | 'crow_handler' | 'notetaker' | any unknown → exclude.
      return null;
  }
}

/**
 * Whether an agent identity is favorited by default.
 * Per the spec (Approach › Crows panel (0)):
 *  - collaborator: favorited by default.
 *  - rogue crows: favorited on creation (treated as default-favorited here, since they
 *    appear in the roster already spawned — the "on creation" event is service-side).
 *  - planners, ticket crows: not default-favorited.
 *
 * C11 seam: this is the derivable "default favorites" logic. The full starring/prefs system
 * (including user-toggled favorites persisted via `tui.save_favorites`) is C11's work.
 * The `CrowChatPanel` uses this to decide which panes to show by default.
 */
export function isDefaultFavorited(identity: AgentIdentity): boolean {
  switch (identity.kind) {
    case 'collaborator':
      return true;
    case 'rogue':
      return true;
    case 'planner':
      return false;
    case 'ticket':
      return false;
    default:
      return identity satisfies never;
  }
}
