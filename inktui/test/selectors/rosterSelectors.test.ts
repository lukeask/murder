/**
 * Roster selector tests — the view-model is a pure function of the slice (rule 2). No store, no bus,
 * no React: feed a known {@link RosterState}, assert the render-ready {@link RosterView}.
 */

import { MODEL_WIDTH, selectRosterView } from '../../src/selectors/rosterSelectors.js';
import type { RosterRow, RosterState } from '../../src/store/roster/rosterSlice.js';

function row(overrides: Partial<RosterRow> = {}): RosterRow {
  return {
    agentId: 'a-1',
    role: 'crow',
    ticketId: 'T-1',
    ticketTitle: 'Title',
    harness: 'claude',
    model: 'anthropic/claude-opus',
    status: 'running',
    session: 'sess-1',
    ...overrides,
  };
}

function state(rows: readonly RosterRow[], overrides: Partial<RosterState> = {}): RosterState {
  return { rows, status: 'ready', error: null, ...overrides };
}

describe('selectRosterView — presentation', () => {
  it('orders rows by status rank, then agent id', () => {
    const view = selectRosterView(
      state([
        row({ agentId: 'idle-b', status: 'idle' }),
        row({ agentId: 'esc', status: 'escalating' }),
        row({ agentId: 'idle-a', status: 'idle' }),
        row({ agentId: 'run', status: 'running' }),
      ]),
    );
    expect(view.rows.map((r) => r.agentId)).toEqual(['esc', 'run', 'idle-a', 'idle-b']);
  });

  it('sorts unknown statuses to the end', () => {
    const view = selectRosterView(
      state([
        row({ agentId: 'weird', status: 'mystery' }),
        row({ agentId: 'run', status: 'running' }),
      ]),
    );
    expect(view.rows.map((r) => r.agentId)).toEqual(['run', 'weird']);
  });

  it('takes the model basename and truncates to width with an ellipsis', () => {
    const long = 'x'.repeat(MODEL_WIDTH + 5);
    const view = selectRosterView(state([row({ model: `provider/${long}` })]));
    expect(view.rows[0]?.model).toHaveLength(MODEL_WIDTH);
    expect(view.rows[0]?.model.endsWith('…')).toBe(true);
  });

  it('fills sentinels for absent harness and model', () => {
    const view = selectRosterView(state([row({ harness: null, model: null })]));
    expect(view.rows[0]?.harness).toBe('—');
    expect(view.rows[0]?.model).toBe('—');
  });

  it('falls back to agent id when no session name is set', () => {
    const view = selectRosterView(state([row({ agentId: 'a-9', session: null })]));
    expect(view.rows[0]?.name).toBe('a-9');
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectRosterView(state([])).isEmpty).toBe(true);
    expect(selectRosterView(state([row()])).isEmpty).toBe(false);
    const err = selectRosterView(state([], { status: 'error', error: 'boom' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('boom');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ agentId: 'b', status: 'idle' }),
      row({ agentId: 'a', status: 'escalating' }),
    ];
    const original = [...rows];
    selectRosterView(state(rows));
    expect(rows).toEqual(original);
  });
});
