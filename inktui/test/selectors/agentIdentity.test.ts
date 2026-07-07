/**
 * Agent identity tests — discriminated-union derivation proof (C10 gate).
 *
 * Proves the tagged union derives correctly for all four kinds (collaborator/planner/rogue/ticket)
 * from `role` + `ticketId` — WITHOUT any string parsing. The tests also verify:
 *  - Infrastructure roles (planning_handler, crow_handler, notetaker) return null (excluded).
 *  - Unknown roles return null (forward-compat).
 *  - `isDefaultFavorited` returns the right value for each kind (collaborator + rogue = true).
 */

import { describe, expect, it } from 'vitest';
import {
  deriveAgentIdentity,
  hasTicket,
  isDefaultFavorited,
  isRogueAgentId,
  planNameFromPlannerAgentId,
  stripSessionPrefix,
} from '../../src/selectors/agentIdentity.js';
import type { RosterRow } from '../../src/store/roster/rosterSlice.js';

// ── Helpers ───────────────────────────────────────────────────────────────────────────────────────

function row(overrides: Partial<RosterRow>): RosterRow {
  return {
    agentId: 'a-1',
    role: 'crow',
    ticketId: null,
    ticketTitle: null,
    harness: null,
    model: null,
    status: 'idle',
    session: null,
    ...overrides,
  };
}

// ── deriveAgentIdentity — four user-facing kinds ──────────────────────────────────────────────────

describe('deriveAgentIdentity — collaborator', () => {
  it('derives collaborator identity from role=collaborator', () => {
    const identity = deriveAgentIdentity(
      row({ role: 'collaborator', agentId: 'collab-1', session: 'collab' }),
    );
    expect(identity).not.toBeNull();
    expect(identity?.kind).toBe('collaborator');
    expect(identity?.agentId).toBe('collab-1');
    expect(identity?.label).toBe('collab'); // session name
  });

  it('falls back to agentId for label when session is null', () => {
    const identity = deriveAgentIdentity(
      row({ role: 'collaborator', agentId: 'collab-x', session: null }),
    );
    expect(identity?.label).toBe('collab-x');
  });
});

describe('deriveAgentIdentity — planner', () => {
  it('derives planner identity from role=planner', () => {
    const identity = deriveAgentIdentity(
      row({ role: 'planner', agentId: 'plan-1', session: 'alpha-plan' }),
    );
    expect(identity).not.toBeNull();
    expect(identity?.kind).toBe('planner');
    expect(identity?.agentId).toBe('plan-1');
    if (identity?.kind === 'planner') {
      expect(identity.plan).toBe('alpha-plan');
    }
  });
});

describe('deriveAgentIdentity — rogue crow (role=crow, ticketId=null)', () => {
  it('derives rogue identity when ticketId is null', () => {
    const identity = deriveAgentIdentity(
      row({ role: 'crow', agentId: 'rogue-1', ticketId: null, session: 'rogue-session' }),
    );
    expect(identity).not.toBeNull();
    expect(identity?.kind).toBe('rogue');
    expect(identity?.agentId).toBe('rogue-1');
    if (identity?.kind === 'rogue') {
      expect(identity.id).toBe('rogue-1'); // id = agentId
    }
  });

  it('no string parsing: identity is derived purely from role + ticketId', () => {
    // The old TUI used session name prefix parsing; verify we don't do that.
    // A crow with a session name that LOOKS like a ticket crow but has ticketId=null is a rogue.
    const identity = deriveAgentIdentity(
      row({
        role: 'crow',
        ticketId: null,
        session: 'ticket-crow-lookalike',
      }),
    );
    expect(identity?.kind).toBe('rogue'); // NOT ticket — ticketId is the authoritative field
  });
});

describe('deriveAgentIdentity — ticket crow (role=crow, ticketId!==null)', () => {
  it('derives ticket identity when ticketId is set', () => {
    const identity = deriveAgentIdentity(
      row({
        role: 'crow',
        agentId: 'ticket-crow-1',
        ticketId: 'T-42',
        ticketTitle: 'Fix the thing',
      }),
    );
    expect(identity).not.toBeNull();
    expect(identity?.kind).toBe('ticket');
    expect(identity?.agentId).toBe('ticket-crow-1');
    if (identity?.kind === 'ticket') {
      expect(identity.id).toBe('T-42');
      expect(identity.label).toBe('Fix the thing'); // ticketTitle as label
    }
  });

  it('falls back to ticketId for label when ticketTitle is null', () => {
    const identity = deriveAgentIdentity(
      row({
        role: 'crow',
        ticketId: 'T-99',
        ticketTitle: null,
      }),
    );
    expect(identity?.kind).toBe('ticket');
    expect(identity?.label).toBe('T-99');
  });
});

// ── Infrastructure/excluded roles → null ─────────────────────────────────────────────────────────

describe('deriveAgentIdentity — excluded roles', () => {
  it.each([
    'planning_handler',
    'crow_handler',
    'notetaker',
  ])('returns null for infrastructure role: %s', (role) => {
    const identity = deriveAgentIdentity(row({ role }));
    expect(identity).toBeNull();
  });

  it('returns null for an unknown role (forward-compat)', () => {
    const identity = deriveAgentIdentity(row({ role: 'future_unknown_role' }));
    expect(identity).toBeNull();
  });
});

// ── isDefaultFavorited ────────────────────────────────────────────────────────────────────────────

describe('isDefaultFavorited', () => {
  it('collaborator is favorited by default', () => {
    const identity = deriveAgentIdentity(row({ role: 'collaborator' }));
    if (identity === null) throw new Error('identity must not be null');
    expect(isDefaultFavorited(identity)).toBe(true);
  });

  it('rogue crow is favorited by default (favorited on creation)', () => {
    const identity = deriveAgentIdentity(row({ role: 'crow', ticketId: null }));
    if (identity === null) throw new Error('identity must not be null');
    expect(isDefaultFavorited(identity)).toBe(true);
  });

  it('planner is NOT favorited by default', () => {
    const identity = deriveAgentIdentity(row({ role: 'planner' }));
    if (identity === null) throw new Error('identity must not be null');
    expect(isDefaultFavorited(identity)).toBe(false);
  });

  it('ticket crow is NOT favorited by default', () => {
    const identity = deriveAgentIdentity(row({ role: 'crow', ticketId: 'T-1' }));
    if (identity === null) throw new Error('identity must not be null');
    expect(isDefaultFavorited(identity)).toBe(false);
  });
});

// ── agentId routing — no conversation_id parsing ─────────────────────────────────────────────────

describe('agentId routing (the anti-pattern replacement)', () => {
  it('agentId is always present in every identity variant', () => {
    const roles: Array<Partial<RosterRow>> = [
      { role: 'collaborator', agentId: 'c-1' },
      { role: 'planner', agentId: 'p-1' },
      { role: 'crow', ticketId: null, agentId: 'r-1' },
      { role: 'crow', ticketId: 'T-1', agentId: 't-1' },
    ];
    for (const overrides of roles) {
      const identity = deriveAgentIdentity(row(overrides));
      expect(identity).not.toBeNull();
      expect(identity?.agentId).toBe(overrides.agentId);
      // agentId is the only field needed for agent.message routing — no parsing required.
    }
  });
});

// ── stripSessionPrefix — the murder_<repo>_<role…>_ prefix peel (item 11) ────────────────────────

describe('stripSessionPrefix — real session-name grammar', () => {
  // Table of real session shapes (from murder/runtime/terminal/session_names.py +
  // orchestrator/runner) → the bare agent name the UI should show.
  const cases: ReadonlyArray<[string, string]> = [
    ['murder_murder_planner_codebase-map', 'codebase-map'],
    ['murder_murder_planning_handler_ctrlsupport', 'ctrlsupport'],
    ['murder_murder_crow_claude_rogue_tony', 'tony'],
    ['murder_murder_crow_codex_rogue_parsingredux', 'parsingredux'],
    ['murder_murder_crow_handler_t047', 't047'],
    ['murder_murder_crow_t050', 't050'],
    ['murder_murder_notetaker_today', 'today'],
    // A plan name with embedded underscores/dashes survives intact (only the prefix peels).
    ['murder_murder_planner_plan-tui-data-render-split', 'plan-tui-data-render-split'],
  ];
  for (const [session, expected] of cases) {
    it(`${session} → ${expected}`, () => {
      expect(stripSessionPrefix(session)).toBe(expected);
    });
  }

  it('returns a bare/non-conforming name unchanged (fall-through, no throw)', () => {
    expect(stripSessionPrefix('murder_murder_collaborator')).toBe('murder_murder_collaborator');
    expect(stripSessionPrefix('something-else')).toBe('something-else');
    expect(stripSessionPrefix('')).toBe('');
  });
});

// ── hasTicket — the rogue-vs-ticket discriminant (item 9a) ───────────────────────────────────────

describe('hasTicket — empty-string ticket_id is NO ticket (item 9a)', () => {
  it('treats null AND empty/whitespace as no-ticket; a real id as a ticket', () => {
    expect(hasTicket(null)).toBe(false);
    expect(hasTicket('')).toBe(false);
    expect(hasTicket('   ')).toBe(false);
    expect(hasTicket('t047')).toBe(true);
  });

  it('a rogue crow with ticketId="" (the real wire payload) derives kind=rogue, not ticket', () => {
    // The backend stores ticket_id='' for rogues; read_model._optional_str('') keeps '', and the
    // slice's `?? null` does not coerce '' → null. So the identity must still be a rogue.
    const identity = deriveAgentIdentity(
      row({
        role: 'crow',
        ticketId: '',
        agentId: 'claude-rogue-tony',
        session: 'murder_murder_crow_claude_rogue_tony',
      }),
    );
    expect(identity?.kind).toBe('rogue');
    expect(identity?.label).toBe('tony');
  });
});

// ── deriveAgentIdentity label — prefix-stripped (item 11) ─────────────────────────────────────────

describe('deriveAgentIdentity — labels are prefix-stripped', () => {
  it('planner label/plan are the bare plan name', () => {
    const identity = deriveAgentIdentity(
      row({ role: 'planner', session: 'murder_murder_planner_codebase-map' }),
    );
    expect(identity?.label).toBe('codebase-map');
  });
});

describe('isRogueAgentId', () => {
  it('matches harness-prefixed rogue ids', () => {
    expect(isRogueAgentId('claude-rogue-tony')).toBe(true);
    expect(isRogueAgentId('codex-rogue-foo')).toBe(true);
  });

  it('rejects ticket crows and planners', () => {
    expect(isRogueAgentId('crow-t001')).toBe(false);
    expect(isRogueAgentId('planner-alpha')).toBe(false);
  });
});

describe('planNameFromPlannerAgentId', () => {
  it('strips the planner- prefix', () => {
    expect(planNameFromPlannerAgentId('planner-alpha')).toBe('alpha');
    expect(planNameFromPlannerAgentId('planner-my-plan')).toBe('my-plan');
  });

  it('returns null for non-planner ids', () => {
    expect(planNameFromPlannerAgentId('claude-rogue-tony')).toBeNull();
  });
});
