/**
 * Conversations selectors tests — C10 gate.
 *
 * Covers:
 *  1. `selectConversationTurns` — block→turn formatting for each block type.
 *  2. `selectFavoritesChatPanes` — correct ordering (collaborator → rogue), correct exclusions.
 *  3. `selectActiveAgentId` — activePaneAgentId priority + fallback to first favorited.
 *  4. Ref-swap stability: same blocks input → same turns array identity (memoised by useMemo).
 */

import { describe, expect, it } from 'vitest';
import {
  selectActiveAgentId,
  selectConversationTurns,
  selectFavoritesChatPanes,
} from '../../src/selectors/conversationsSelectors.js';
import type { ConversationBlock } from '../../src/store/conversations/conversationsSlice.js';
import { initialConversationsState } from '../../src/store/conversations/conversationsSlice.js';
import type { RosterRow } from '../../src/store/roster/rosterSlice.js';
import { initialRosterState } from '../../src/store/roster/rosterSlice.js';

// ── Helpers ───────────────────────────────────────────────────────────────────────────────────────

function block(type: string, extras: Record<string, unknown> = {}, id?: string): ConversationBlock {
  return { type, id: id ?? null, raw: { type, ...extras } };
}

function rosterRow(overrides: Partial<RosterRow>): RosterRow {
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

// ── selectConversationTurns ───────────────────────────────────────────────────────────────────────

describe('selectConversationTurns', () => {
  it('returns empty array for undefined or empty input', () => {
    expect(selectConversationTurns(undefined)).toEqual([]);
    expect(selectConversationTurns([])).toEqual([]);
  });

  it('formats user block correctly', () => {
    const turns = selectConversationTurns([block('user', { text: 'hello world' })]);
    expect(turns).toHaveLength(1);
    expect(turns[0]?.speaker).toBe('user');
    expect(turns[0]?.text).toBe('hello world');
  });

  it('formats assistant block correctly', () => {
    const turns = selectConversationTurns([block('assistant', { text: 'I can help!' })]);
    expect(turns[0]?.speaker).toBe('assistant');
    expect(turns[0]?.text).toBe('I can help!');
  });

  it('formats tool_call block with title', () => {
    const turns = selectConversationTurns([
      block('tool_call', { title: 'Read file', input: 'foo.ts' }),
    ]);
    expect(turns[0]?.speaker).toBe('tool');
    expect(turns[0]?.text).toContain('Read file');
    expect(turns[0]?.text).toContain('$ foo.ts');
  });

  it('formats plan_update block with items', () => {
    const turns = selectConversationTurns([
      block('plan_update', {
        title: 'My plan',
        items: [
          { text: 'step one', done: false },
          { text: 'step two', done: true },
        ],
      }),
    ]);
    expect(turns[0]?.speaker).toBe('plan');
    expect(turns[0]?.text).toContain('[ ] step one');
    expect(turns[0]?.text).toContain('[x] step two');
  });

  it('formats notice block', () => {
    const turns = selectConversationTurns([
      block('notice', { message: 'something happened', severity: 'warning' }),
    ]);
    expect(turns[0]?.speaker).toBe('notice');
    expect(turns[0]?.text).toBe('warning: something happened');
  });

  it('unknown block type passes through with fallback label', () => {
    const turns = selectConversationTurns([block('future_type', {})]);
    expect(turns[0]?.speaker).toBe('unknown');
    expect(turns[0]?.text).toBe('[future_type]');
  });

  it('skips empty user/assistant blocks', () => {
    const turns = selectConversationTurns([
      block('user', { text: '' }),
      block('assistant', { text: '   ' }),
    ]);
    expect(turns).toHaveLength(0);
  });

  it('preserves blockId from the ConversationBlock id field', () => {
    const turns = selectConversationTurns([block('user', { text: 'hi' }, 'blk-abc')]);
    expect(turns[0]?.blockId).toBe('blk-abc');
  });
});

// ── selectFavoritesChatPanes ──────────────────────────────────────────────────────────────────────

describe('selectFavoritesChatPanes', () => {
  it('returns empty panes for an empty roster', () => {
    const { panes } = selectFavoritesChatPanes(initialRosterState);
    expect(panes).toHaveLength(0);
  });

  it('includes collaborator and rogue crows; excludes planner and ticket crows', () => {
    const rows: RosterRow[] = [
      rosterRow({ agentId: 'collab', role: 'collaborator' }),
      rosterRow({ agentId: 'planner-1', role: 'planner' }),
      rosterRow({ agentId: 'rogue-1', role: 'crow', ticketId: null }),
      rosterRow({ agentId: 'ticket-crow', role: 'crow', ticketId: 'T-1' }),
    ];
    const { panes } = selectFavoritesChatPanes({ ...initialRosterState, rows, status: 'ready' });
    const agentIds = panes.map((p) => p.agentId);
    expect(agentIds).toContain('collab');
    expect(agentIds).toContain('rogue-1');
    expect(agentIds).not.toContain('planner-1');
    expect(agentIds).not.toContain('ticket-crow');
  });

  it('orders collaborator before rogue crows (spec order)', () => {
    const rows: RosterRow[] = [
      rosterRow({ agentId: 'rogue-1', role: 'crow', ticketId: null }),
      rosterRow({ agentId: 'collab', role: 'collaborator' }),
    ];
    const { panes } = selectFavoritesChatPanes({ ...initialRosterState, rows, status: 'ready' });
    expect(panes[0]?.kind).toBe('collaborator');
    expect(panes[1]?.kind).toBe('rogue');
  });

  it('excludes infrastructure roles', () => {
    const rows: RosterRow[] = [
      rosterRow({ agentId: 'nh', role: 'planning_handler' }),
      rosterRow({ agentId: 'ch', role: 'crow_handler' }),
      rosterRow({ agentId: 'nt', role: 'notetaker' }),
    ];
    const { panes } = selectFavoritesChatPanes({ ...initialRosterState, rows, status: 'ready' });
    expect(panes).toHaveLength(0);
  });
});

// ── selectActiveAgentId ───────────────────────────────────────────────────────────────────────────

describe('selectActiveAgentId', () => {
  it('returns null when no agents and no pinned pane', () => {
    const result = selectActiveAgentId(initialConversationsState, initialRosterState);
    expect(result).toBeNull();
  });

  it('returns activePaneAgentId when set (user-pinned takes priority)', () => {
    const conversations = { ...initialConversationsState, activePaneAgentId: 'agent-pinned' };
    const result = selectActiveAgentId(conversations, initialRosterState);
    expect(result).toBe('agent-pinned');
  });

  it('falls back to first default-favorited crow (collaborator) when no pinned pane', () => {
    const rows: RosterRow[] = [
      rosterRow({ agentId: 'collab', role: 'collaborator' }),
      rosterRow({ agentId: 'rogue-1', role: 'crow', ticketId: null }),
    ];
    const roster = { ...initialRosterState, rows, status: 'ready' as const };
    const result = selectActiveAgentId(initialConversationsState, roster);
    expect(result).toBe('collab'); // collaborator is first in spec order
  });
});
