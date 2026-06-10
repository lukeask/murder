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
import { deriveAgentIdentity } from '../../src/selectors/agentIdentity.js';
import {
  isChatPaneOpen,
  selectActiveAgentId,
  selectConversationTurns,
  selectCycledTarget,
  selectCycleTargets,
  selectFavoritesChatPanes,
  selectOpenChatPanes,
} from '../../src/selectors/conversationsSelectors.js';
import type { ConversationBlock } from '../../src/store/conversations/conversationsSlice.js';
import { initialConversationsState } from '../../src/store/conversations/conversationsSlice.js';
import type { FavoritesState } from '../../src/store/favorites/favoritesSlice.js';
import { initialFavoritesState } from '../../src/store/favorites/favoritesSlice.js';
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

  it('formats agent_event as "status · name · elapsed", dropping empties', () => {
    const turns = selectConversationTurns([
      block('agent_event', { status: 'running', name: 'crow-7', elapsed: '12s' }),
    ]);
    expect(turns[0]?.speaker).toBe('agent');
    expect(turns[0]?.text).toBe('running · crow-7 · 12s');
  });

  it('agent_event drops empty parts and preserves status · name · elapsed order', () => {
    const turns = selectConversationTurns([
      block('agent_event', { status: '', name: 'crow-7', elapsed: '' }),
    ]);
    expect(turns[0]?.text).toBe('crow-7');
  });

  it('agent_event with all-empty parts is skipped', () => {
    const turns = selectConversationTurns([
      block('agent_event', { status: '  ', name: '', elapsed: '' }),
    ]);
    expect(turns).toHaveLength(0);
  });

  it('formats answered choice_prompt showing only the chosen option', () => {
    const turns = selectConversationTurns([
      block('choice_prompt', {
        question: 'Pick one',
        answered: true,
        chosen: 2,
        options: [
          { number: 1, label: 'Alpha' },
          { number: 2, label: 'Beta' },
        ],
      }),
    ]);
    expect(turns[0]?.speaker).toBe('prompt');
    expect(turns[0]?.text).toBe('Pick one\nselected: 2. Beta');
    expect(turns[0]?.isLivePrompt).toBeUndefined();
  });

  it('formats unanswered choice_prompt listing every numbered option', () => {
    const turns = selectConversationTurns([
      block('user', { text: 'go' }),
      block('choice_prompt', {
        question: 'Pick one',
        options: [
          { number: 1, label: 'Alpha' },
          { number: 2, label: 'Beta' },
        ],
      }),
    ]);
    // Covers the unanswered listing text (every numbered option). This prompt is trailing,
    // so it also carries isLivePrompt=true — asserted separately in the heuristic tests below.
    const prompt = turns.find((t) => t.speaker === 'prompt');
    expect(prompt?.text).toBe('Pick one\n1. Alpha\n2. Beta');
  });

  it('choice_prompt with no question is skipped', () => {
    const turns = selectConversationTurns([
      block('choice_prompt', { question: '  ', options: [] }),
    ]);
    expect(turns).toHaveLength(0);
  });

  it('marks a trailing unanswered choice_prompt as a live prompt (heuristic)', () => {
    const turns = selectConversationTurns([
      block('assistant', { text: 'thinking' }),
      block('choice_prompt', {
        question: 'Pick one',
        options: [{ number: 1, label: 'Alpha' }],
      }),
    ]);
    const last = turns[turns.length - 1];
    expect(last?.speaker).toBe('prompt');
    expect(last?.isLivePrompt).toBe(true);
  });

  it('does NOT mark an answered trailing choice_prompt as live', () => {
    const turns = selectConversationTurns([
      block('choice_prompt', {
        question: 'Pick one',
        answered: true,
        chosen: 1,
        options: [{ number: 1, label: 'Alpha' }],
      }),
    ]);
    expect(turns[turns.length - 1]?.isLivePrompt).toBeUndefined();
  });

  it('does NOT mark a non-trailing unanswered choice_prompt as live', () => {
    const turns = selectConversationTurns([
      block('choice_prompt', {
        question: 'Pick one',
        options: [{ number: 1, label: 'Alpha' }],
      }),
      block('assistant', { text: 'moved on' }),
    ]);
    const prompt = turns.find((t) => t.speaker === 'prompt');
    expect(prompt?.isLivePrompt).toBeUndefined();
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

// ── isChatPaneOpen / selectOpenChatPanes — pane open/close model (item 9b) ───────────────────────

function favSet(...ids: string[]): FavoritesState {
  return { ...initialFavoritesState, ids: new Set(ids), status: 'ready' };
}

describe('isChatPaneOpen — favorites default merged with overrides', () => {
  const rogue = deriveAgentIdentity(rosterRow({ role: 'crow', ticketId: null, agentId: 'r1' }))!;
  const planner = deriveAgentIdentity(rosterRow({ role: 'planner', agentId: 'p1' }))!;

  it('default-favorited (rogue) is open with no override', () => {
    expect(isChatPaneOpen(rogue, initialFavoritesState, new Map())).toBe(true);
  });

  it('non-favorited (planner) is closed with no override', () => {
    expect(isChatPaneOpen(planner, initialFavoritesState, new Map())).toBe(false);
  });

  it('override true opens a non-favorited planner', () => {
    expect(isChatPaneOpen(planner, initialFavoritesState, new Map([['p1', true]]))).toBe(true);
  });

  it('override false closes a default-favorited rogue', () => {
    expect(isChatPaneOpen(rogue, initialFavoritesState, new Map([['r1', false]]))).toBe(false);
  });

  it('explicit star (favorites set) opens a planner with no override', () => {
    expect(isChatPaneOpen(planner, favSet('p1'), new Map())).toBe(true);
  });
});

describe('selectOpenChatPanes — open set = favorites default + overrides', () => {
  const rows: RosterRow[] = [
    rosterRow({ role: 'collaborator', agentId: 'collab', session: 'collab' }),
    rosterRow({ role: 'planner', agentId: 'p1', session: 'murder_murder_planner_alpha' }),
    rosterRow({
      role: 'crow',
      ticketId: null,
      agentId: 'r1',
      session: 'murder_murder_crow_claude_rogue_tony',
    }),
  ];
  const roster = { ...initialRosterState, rows, status: 'ready' as const };

  it('defaults: collaborator + rogue open, planner closed', () => {
    const { panes } = selectOpenChatPanes(roster, initialFavoritesState, new Map());
    expect(panes.map((p) => p.agentId)).toEqual(['collab', 'r1']);
  });

  it('override opens the planner, in spec order (collaborator → planner → rogue)', () => {
    const { panes } = selectOpenChatPanes(roster, initialFavoritesState, new Map([['p1', true]]));
    expect(panes.map((p) => p.agentId)).toEqual(['collab', 'p1', 'r1']);
  });

  it('override closes the default-favorited rogue', () => {
    const { panes } = selectOpenChatPanes(roster, initialFavoritesState, new Map([['r1', false]]));
    expect(panes.map((p) => p.agentId)).toEqual(['collab']);
  });
});

// ── selectCycleTargets / selectCycledTarget — chat-target cycling (item 9 super-chords) ───────────

describe('chat-target cycling (item 9)', () => {
  const rows: RosterRow[] = [
    rosterRow({ role: 'collaborator', agentId: 'collab', session: 'collab' }),
    rosterRow({ role: 'planner', agentId: 'p1', session: 'murder_murder_planner_alpha' }),
    rosterRow({
      role: 'crow',
      ticketId: null,
      agentId: 'r1',
      session: 'murder_murder_crow_claude_rogue_tony',
    }),
  ];
  const roster = { ...initialRosterState, rows, status: 'ready' as const };

  it('cycle order = open panes (Stage order) then favorited crows whose panes are closed', () => {
    // p1 (planner) is favorited but its pane is overridden CLOSED → it lands in the closed-favorite
    // tail after the open collab + rogue panes.
    const conversations = {
      ...initialConversationsState,
      paneOverrides: new Map([['p1', false]]),
    };
    const targets = selectCycleTargets(conversations, roster, favSet('p1'));
    expect(targets.map((t) => t.agentId)).toEqual(['collab', 'r1', 'p1']);
  });

  it('a favorite that is open (default) appears once in the open section, not the tail', () => {
    // p1 favorited → open by default → in the open section between collab and r1; not duplicated.
    const targets = selectCycleTargets(initialConversationsState, roster, favSet('p1'));
    expect(targets.map((t) => t.agentId)).toEqual(['collab', 'p1', 'r1']);
  });

  it('next steps forward through the cycle from the current target', () => {
    // Active = collab (first open pane). Next → r1.
    const result = selectCycledTarget(initialConversationsState, roster, initialFavoritesState, 1);
    expect(result).toEqual({ agentId: 'r1', needsOpen: false });
  });

  it('prev wraps around to the last entry', () => {
    // Active = collab; prev wraps to the last cycle target (r1 — the only other open pane).
    const result = selectCycledTarget(initialConversationsState, roster, initialFavoritesState, -1);
    expect(result).toEqual({ agentId: 'r1', needsOpen: false });
  });

  it('landing on a closed-pane favorite flags needsOpen', () => {
    // p1 favorited but overridden CLOSED → cycle = [collab, r1, p1]. From r1 (active), next = p1,
    // whose pane is closed, so the caller must open it.
    const conversations = {
      ...initialConversationsState,
      activePaneAgentId: 'r1',
      paneOverrides: new Map([['p1', false]]),
    };
    const result = selectCycledTarget(conversations, roster, favSet('p1'), 1);
    expect(result).toEqual({ agentId: 'p1', needsOpen: true });
  });

  it('returns null when there is nothing to cycle to', () => {
    const result = selectCycledTarget(
      initialConversationsState,
      initialRosterState,
      initialFavoritesState,
      1,
    );
    expect(result).toBeNull();
  });
});
