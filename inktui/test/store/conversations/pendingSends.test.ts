/**
 * Optimistic shadow-turn reconciliation and view merge.
 */

import { describe, expect, it } from 'vitest';
import {
  confirmedClientMessageIds,
  reconcilePendingByAgent,
} from '../../../src/store/conversations/conversationsActions.js';
import type {
  ConversationBlock,
  PendingSend,
} from '../../../src/store/conversations/conversationsSlice.js';
import { initialConversationsState } from '../../../src/store/conversations/conversationsSlice.js';
import {
  selectConversationView,
  selectMergedConversationTurns,
  selectPendingTurns,
} from '../../../src/selectors/conversationsSelectors.js';

function block(type: string, extras: Record<string, unknown> = {}, id?: string): ConversationBlock {
  return { type, id: id ?? null, raw: { type, ...extras } };
}

function pending(overrides: Partial<PendingSend> = {}): PendingSend {
  return {
    clientId: 'c1',
    agentId: 'crow-1',
    text: 'hello',
    createdAt: 1,
    status: 'sending',
    ...overrides,
  };
}

describe('confirmedClientMessageIds', () => {
  it('collects client_message_id values from user blocks only', () => {
    const ids = confirmedClientMessageIds({
      'crow-1': [
        block('user', { text: 'a', client_message_id: 'c1' }, '1'),
        block('assistant', { text: 'b', client_message_id: 'nope' }, '2'),
        block('user', { text: 'c' }, '3'),
        block('user', { text: 'd', client_message_id: 'c2' }, '4'),
      ],
    });
    expect([...ids].sort()).toEqual(['c1', 'c2']);
  });
});

describe('reconcilePendingByAgent', () => {
  it('drops pending items whose clientId is confirmed', () => {
    const next = reconcilePendingByAgent(
      {
        'crow-1': [pending({ clientId: 'c1' }), pending({ clientId: 'c2', text: 'other' })],
        'crow-2': [pending({ clientId: 'c3', agentId: 'crow-2' })],
      },
      new Set(['c1', 'c3']),
    );
    expect(next).toEqual({
      'crow-1': [pending({ clientId: 'c2', text: 'other' })],
    });
  });

  it('returns the same map when nothing is confirmed', () => {
    const pendingByAgent = { 'crow-1': [pending()] };
    expect(reconcilePendingByAgent(pendingByAgent, new Set())).toBe(pendingByAgent);
  });
});

describe('selectMergedConversationTurns', () => {
  it('appends pending shadow turns after authoritative turns', () => {
    const turns = selectMergedConversationTurns(
      [block('user', { text: 'solid' }, '1'), block('assistant', { text: 'ok' }, '2')],
      [pending({ clientId: 'p1', text: 'shadow', status: 'accepted' })],
    );
    expect(turns.map((t) => ({ text: t.text, delivery: t.delivery, blockId: t.blockId }))).toEqual([
      { text: 'solid', delivery: undefined, blockId: '1' },
      { text: 'ok', delivery: undefined, blockId: '2' },
      { text: 'shadow', delivery: 'accepted', blockId: 'pending:p1' },
    ]);
  });

  it('selectPendingTurns maps delivery status onto user turns', () => {
    expect(selectPendingTurns([pending({ status: 'queued' })])).toEqual([
      {
        speaker: 'user',
        text: 'hello',
        blockId: 'pending:c1',
        delivery: 'queued',
      },
    ]);
  });
});

describe('selectConversationView pending merge', () => {
  it('includes pending turns from state.pendingByAgent', () => {
    const view = selectConversationView('crow-1', {
      ...initialConversationsState,
      transcripts: {
        'crow-1': [block('assistant', { text: 'prior' }, '9')],
      },
      pendingByAgent: {
        'crow-1': [pending({ text: 'just typed', status: 'sending' })],
      },
    });
    expect(view.turns.map((t) => t.text)).toEqual(['prior', 'just typed']);
    expect(view.turns[1]?.delivery).toBe('sending');
  });
});
