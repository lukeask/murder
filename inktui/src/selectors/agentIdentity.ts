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
  switch (row.role) {
    case 'collaborator':
      return {
        kind: 'collaborator',
        agentId: row.agentId,
        label: row.session ?? row.agentId,
      };
    case 'planner':
      return {
        kind: 'planner',
        agentId: row.agentId,
        label: row.session ?? row.agentId,
        plan: row.session ?? row.agentId,
      };
    case 'crow':
      if (row.ticketId === null) {
        return {
          kind: 'rogue',
          agentId: row.agentId,
          label: row.session ?? row.agentId,
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
