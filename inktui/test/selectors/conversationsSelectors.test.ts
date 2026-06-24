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
  condenseBlocks,
  isChatPaneOpen,
  selectActiveAgentId,
  selectAdjacentTargets,
  selectConversationTurns,
  selectConversationView,
  selectCycledTarget,
  selectCycleTargets,
  selectFavoritesChatPanes,
  selectOpenChatPanes,
} from '../../src/selectors/conversationsSelectors.js';
import type {
  ChunkSummary,
  ConversationBlock,
  ConversationsState,
} from '../../src/store/conversations/conversationsSlice.js';
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

  it('cycle = EVERY chattable crow in spec order, regardless of pane open/closed', () => {
    // Cycling is pure input routing now (it no longer opens panes), so the universe is all chattable
    // crows in spec order (collaborator → planner → rogue → ticket) — pane overrides do not gate it.
    const conversations = {
      ...initialConversationsState,
      paneOverrides: new Map([['p1', false]]),
    };
    const targets = selectCycleTargets(conversations, roster, favSet('p1'));
    expect(targets.map((t) => t.agentId)).toEqual(['collab', 'p1', 'r1']);
  });

  it('reaches a non-favorited crow whose pane is closed (no favorites needed)', () => {
    // No favorites, no overrides: the planner p1 is neither default-favorited nor pinned, yet it is
    // still in the cycle (the user can target it without its chat box being on the Stage).
    const targets = selectCycleTargets(initialConversationsState, roster, initialFavoritesState);
    expect(targets.map((t) => t.agentId)).toEqual(['collab', 'p1', 'r1']);
  });

  it('next steps forward through the cycle from the current target', () => {
    // Active = collab (first open pane). Next → p1 (planner) — its pane is closed but cycling reaches
    // it anyway; needsOpen reports the closed state but the caller no longer opens it.
    const result = selectCycledTarget(initialConversationsState, roster, initialFavoritesState, 1);
    expect(result).toEqual({ agentId: 'p1', needsOpen: true });
  });

  it('prev wraps around to the last entry', () => {
    // Active = collab; prev wraps to the last cycle target (r1 — the rogue, default-favorited → open).
    const result = selectCycledTarget(initialConversationsState, roster, initialFavoritesState, -1);
    expect(result).toEqual({ agentId: 'r1', needsOpen: false });
  });

  it('next wraps from the last entry back to the first', () => {
    // Active pinned to r1 (last in spec order) → next wraps to collab (first).
    const conversations = { ...initialConversationsState, activePaneAgentId: 'r1' };
    const result = selectCycledTarget(conversations, roster, initialFavoritesState, 1);
    expect(result).toEqual({ agentId: 'collab', needsOpen: false });
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

  it('selectAdjacentTargets names the prev/next crows around the current target', () => {
    // Active = collab (index 0 of [collab, p1, r1]) → prev wraps to r1, next is p1.
    const { prev, next } = selectAdjacentTargets(
      initialConversationsState,
      roster,
      initialFavoritesState,
    );
    expect(prev?.agentId).toBe('r1');
    expect(next?.agentId).toBe('p1');
  });

  it('selectAdjacentTargets returns null/null when fewer than two crows', () => {
    const soloRoster = {
      ...initialRosterState,
      rows: [rosterRow({ role: 'collaborator', agentId: 'collab', session: 'collab' })],
      status: 'ready' as const,
    };
    const { prev, next } = selectAdjacentTargets(
      initialConversationsState,
      soloRoster,
      initialFavoritesState,
    );
    expect(prev).toBeNull();
    expect(next).toBeNull();
  });
});

// ── Condensed view (TUIchat-4): attribution-driven block replacement ────────────────────────────────

function summary(
  summaryId: number,
  chunkIdx: number,
  summaryText: string,
  blockIds: number[],
): ChunkSummary {
  return { summaryId, chunkIdx, summary: summaryText, blockIds };
}

/** Build a ConversationsState with one agent's transcript + chunk summaries for condensed tests. */
function condensedState(
  agentId: string,
  blocks: readonly ConversationBlock[],
  summaries: readonly ChunkSummary[],
): ConversationsState {
  return {
    ...initialConversationsState,
    transcripts: { [agentId]: blocks },
    chunkSummaries: { [agentId]: summaries },
  };
}

describe('condenseBlocks (TUIchat-4 attribution)', () => {
  it('replaces the run of attributed blocks with a single summary block', () => {
    const blocks = [
      block('assistant', { text: 'thinking step 1' }, '1'),
      block('tool_call', { title: 'Read foo.ts' }, '2'),
      block('assistant', { text: 'thinking step 2' }, '3'),
    ];
    const out = condenseBlocks(blocks, [summary(10, 0, 'Investigated foo.ts', [1, 2, 3])]);
    expect(out).toHaveLength(1);
    expect(out?.[0]?.type).toBe('__condensed_summary__');
    expect(out?.[0]?.raw['text']).toBe('Investigated foo.ts');
    expect(selectConversationTurns(out)[0]?.tone).toBe('summary');
  });

  it('attribution is by block id, not position — only listed ids are replaced', () => {
    const blocks = [
      block('assistant', { text: 'covered A' }, '1'),
      block('assistant', { text: 'UNCOVERED tail' }, '2'),
      block('assistant', { text: 'covered B' }, '3'),
    ];
    // Summary covers ids 1 and 3 only; id 2 is the still-buffering tail and renders as-is.
    const out = condenseBlocks(blocks, [summary(10, 0, 'A-and-B summary', [1, 3])]);
    // The summary anchors at the earliest covered position (id 1), then id 2 verbatim. id 3 dropped.
    const turns = selectConversationTurns(out);
    expect(turns.map((t) => t.text)).toEqual(['A-and-B summary', 'UNCOVERED tail']);
  });

  it('orders summaries by their attributed block positions (chunk order preserved)', () => {
    const blocks = [
      block('assistant', { text: 'i1' }, '1'),
      block('assistant', { text: 'i2' }, '2'),
      block('assistant', { text: 'i3' }, '3'),
      block('assistant', { text: 'i4' }, '4'),
    ];
    const out = condenseBlocks(blocks, [
      summary(20, 0, 'first chunk', [1, 2]),
      summary(21, 1, 'second chunk', [3, 4]),
    ]);
    const turns = selectConversationTurns(out);
    expect(turns.map((t) => t.text)).toEqual(['first chunk', 'second chunk']);
  });

  it('NEVER replaces the final reply — assistant_final is unattributed and stays verbatim', () => {
    const blocks = [
      block('assistant', { text: 'intermediate work' }, '1'),
      block('assistant', { text: 'THE FINAL ANSWER' }, '2'), // never in any block_ids
    ];
    const out = condenseBlocks(blocks, [summary(10, 0, 'did some work', [1])]);
    const turns = selectConversationTurns(out);
    expect(turns.map((t) => t.text)).toEqual(['did some work', 'THE FINAL ANSWER']);
  });

  it('preserves the entire trailing final assistant run even if a summary references part of it', () => {
    const blocks: ConversationBlock[] = [
      block('assistant', { text: 'intermediate work' }, '1'),
      block('tool_call', { title: 'Edit x' }, '2'),
      {
        ...block('assistant', { phase: 'final', text: 'FINAL SECTION A' }, '3'),
        kind: 'assistant_final',
      },
      {
        ...block('assistant', { phase: 'final', text: 'FINAL SECTION B' }, '4'),
        kind: 'assistant_final',
      },
    ];
    const out = condenseBlocks(blocks, [summary(10, 0, 'did some work', [1, 2, 3])]);
    const turns = selectConversationTurns(out);
    expect(turns.map((t) => t.text)).toEqual([
      'did some work',
      'FINAL SECTION A',
      'FINAL SECTION B',
    ]);
  });

  it('falls back to verbose-like (returns blocks unchanged) when there are no summaries', () => {
    const blocks = [block('assistant', { text: 'a' }, '1'), block('assistant', { text: 'b' }, '2')];
    expect(condenseBlocks(blocks, [])).toBe(blocks);
    expect(condenseBlocks(blocks, undefined)).toBe(blocks);
  });

  it('keeps blocks with no matching summary id (graceful, never blank)', () => {
    const blocks = [block('assistant', { text: 'orphan' }, '99')];
    // Summary references ids that aren't present → nothing is replaced, content survives.
    const out = condenseBlocks(blocks, [summary(10, 0, 'unrelated', [1, 2])]);
    expect(selectConversationTurns(out).map((t) => t.text)).toEqual(['orphan']);
  });
});

describe('selectConversationView view modes (TUIchat-4)', () => {
  const blocks = [
    block('assistant', { text: 'step one' }, '1'),
    block('tool_call', { title: 'Edit x' }, '2'),
    block('assistant', { text: 'FINAL' }, '3'),
  ];
  const summaries = [summary(10, 0, 'condensed work', [1, 2])];

  it('condensed mode replaces attributed blocks with the summary', () => {
    const state = condensedState('a-1', blocks, summaries);
    const view = selectConversationView('a-1', state, 'condensed');
    expect(view.turns.map((t) => t.text)).toEqual(['condensed work', 'FINAL']);
    expect(view.hasContent).toBe(true);
  });

  it('verbose mode is byte-identical to the default (no summary applied)', () => {
    const state = condensedState('a-1', blocks, summaries);
    const verbose = selectConversationView('a-1', state, 'verbose');
    const dflt = selectConversationView('a-1', state);
    expect(verbose.turns).toEqual(dflt.turns);
    // Verbose shows every intermediate verbatim — the summary text never appears.
    expect(verbose.turns.map((t) => t.text)).toEqual(['step one', 'Edit x', 'FINAL']);
  });

  it('condensed with no summaries degrades to the verbose render', () => {
    const state = condensedState('a-1', blocks, []);
    const condensed = selectConversationView('a-1', state, 'condensed');
    const verbose = selectConversationView('a-1', state, 'verbose');
    expect(condensed.turns).toEqual(verbose.turns);
  });
});
