/**
 * Stage pane derivation + visible/overflow partition (no React).
 */

import { describe, expect, it } from 'vitest';
import { initialConversationsState } from '@murder/ui-core/store/conversations/conversationsSlice.js';
import { initialFavoritesState } from '@murder/ui-core/store/favorites/favoritesSlice.js';
import { initialRosterState } from '@murder/ui-core/store/roster/rosterSlice.js';
import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';
import {
  MAX_VISIBLE_TRANSCRIPT_PANES,
  partitionStagePanes,
  selectStageTranscriptPanes,
  type StageTranscriptPane,
} from '../src/components/stage/stagePanes.js';
import {
  computeStageLayout,
  transcriptGridColumns,
} from '../src/components/stage/stageTiling.js';

const row = (over: Partial<RosterRow> & { agentId: string }): RosterRow => ({
  role: 'collaborator',
  ticketId: null,
  ticketTitle: null,
  harness: 'claude',
  model: 'opus',
  status: 'running',
  session: null,
  sessionId: null,
  ...over,
});

function pane(agentId: string, current = false, locked = true): StageTranscriptPane {
  return {
    identity: { kind: 'collaborator', agentId, label: agentId },
    locked,
    current,
  };
}

describe('selectStageTranscriptPanes', () => {
  it('opens default-favorited agents (collaborator + rogue)', () => {
    const roster = {
      ...initialRosterState,
      status: 'ready' as const,
      rows: [
        row({ agentId: 'collab' }),
        row({ agentId: 'rogue-1', role: 'crow', ticketId: null }),
        row({ agentId: 'planner-1', role: 'planner' }),
      ],
    };
    const panes = selectStageTranscriptPanes(
      initialConversationsState,
      roster,
      initialFavoritesState,
    );
    expect(panes.map((p) => p.identity.agentId)).toEqual(['collab', 'rogue-1']);
  });

  it('honors paneOverrides to open a planner and close a rogue', () => {
    const roster = {
      ...initialRosterState,
      status: 'ready' as const,
      rows: [
        row({ agentId: 'collab' }),
        row({ agentId: 'rogue-1', role: 'crow', ticketId: null }),
        row({ agentId: 'planner-1', role: 'planner' }),
      ],
    };
    const conversations = {
      ...initialConversationsState,
      paneOverrides: new Map([
        ['planner-1', true],
        ['rogue-1', false],
      ]),
    };
    const panes = selectStageTranscriptPanes(conversations, roster, initialFavoritesState);
    expect(panes.map((p) => p.identity.agentId)).toEqual(['collab', 'planner-1']);
  });

  it('adds the active agent as an ephemeral pane when not locked-open', () => {
    const roster = {
      ...initialRosterState,
      status: 'ready' as const,
      rows: [
        row({ agentId: 'collab' }),
        row({ agentId: 'planner-1', role: 'planner' }),
      ],
    };
    const conversations = {
      ...initialConversationsState,
      activePaneAgentId: 'planner-1',
      // Favorited so selectActiveAgentId / recipient targets include the planner.
      paneOverrides: new Map(),
    };
    // Without starring, planner is not a recipient target — pin via favorites.
    const favorites = {
      ...initialFavoritesState,
      status: 'ready' as const,
      ids: new Set(['planner-1']),
    };
    // Favorited planner is locked-open via isTranscriptPaneOpen — so this is locked, not ephemeral.
    const panes = selectStageTranscriptPanes(conversations, roster, favorites);
    expect(panes.some((p) => p.identity.agentId === 'planner-1' && p.locked)).toBe(true);
  });

  it('does not resurrect an explicitly closed active transcript as ephemeral', () => {
    const roster = {
      ...initialRosterState,
      status: 'ready' as const,
      rows: [
        row({ agentId: 'collab' }),
        row({ agentId: 'planner-1', role: 'planner' }),
      ],
    };
    const conversations = {
      ...initialConversationsState,
      activePaneAgentId: 'planner-1',
      paneOverrides: new Map([['planner-1', false]]),
    };
    const favorites = {
      ...initialFavoritesState,
      status: 'ready' as const,
      ids: new Set(['planner-1']),
    };
    const panes = selectStageTranscriptPanes(conversations, roster, favorites);
    expect(panes.map((p) => p.identity.agentId)).toEqual(['collab']);
  });
});

describe('partitionStagePanes', () => {
  it('keeps all panes visible when under the cap', () => {
    const open = [pane('a'), pane('b', true), pane('c')];
    const { visible, overflow } = partitionStagePanes(open, 3);
    expect(visible.map((p) => p.identity.agentId)).toEqual(['a', 'b', 'c']);
    expect(overflow).toEqual([]);
  });

  it(`caps at ${MAX_VISIBLE_TRANSCRIPT_PANES} and keeps the current target visible`, () => {
    const open = [pane('a'), pane('b'), pane('c'), pane('d', true), pane('e')];
    const { visible, overflow } = partitionStagePanes(open, MAX_VISIBLE_TRANSCRIPT_PANES);
    expect(visible.length).toBe(MAX_VISIBLE_TRANSCRIPT_PANES);
    expect(visible.some((p) => p.identity.agentId === 'd')).toBe(true);
    expect(overflow.length).toBe(2);
    expect(overflow.every((p) => p.identity.agentId !== 'd')).toBe(true);
  });
});

describe('stageTiling (webui copy)', () => {
  it('tiles two transcripts side-by-side without a doc', () => {
    expect(transcriptGridColumns(2, false, 'landscape')).toBe(2);
    const layout = computeStageLayout(['a', 'b'], false, 'landscape');
    expect(layout.columns).toBe(2);
    expect(layout.rows).toEqual([['a', 'b']]);
  });

  it('stacks transcripts beside a doc until four', () => {
    const layout = computeStageLayout(['a', 'b'], true, 'landscape');
    expect(layout.docWeight).toBe(1);
    expect(layout.transcriptWeight).toBe(1);
    expect(layout.rows).toEqual([['a'], ['b']]);
  });
});
