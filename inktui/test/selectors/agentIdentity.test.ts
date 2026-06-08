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
import { deriveAgentIdentity, isDefaultFavorited } from '../../src/selectors/agentIdentity.js';
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
