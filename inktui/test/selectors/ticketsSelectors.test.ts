/**
 * Tickets selector tests — the view-model is a pure function of the slice (rule 2).
 * Copied from {@link ./notesSelectors.test.ts}. No store, no bus, no React.
 *
 * Rule 2 proof: every column-formatting path (deps cell, schedule cell, harness/model,
 * alternating parity) is exercised here, proving the component has nothing left to format.
 */

import { selectTicketsView } from '../../src/selectors/ticketsSelectors.js';
import type { TicketRow, TicketsState } from '../../src/store/tickets/ticketsSlice.js';

function row(overrides: Partial<TicketRow> = {}): TicketRow {
  return {
    id: 'T-1',
    title: 'Alpha ticket',
    status: 'ready',
    lastUpdateAt: '2026-06-01T10:00:00',
    lastUpdateLabel: 'agent summarized',
    scheduleAt: null,
    harness: 'claude',
    model: 'anthropic/claude-opus',
    pendingDepIds: [],
    parent: null,
    ...overrides,
  };
}

function state(rows: readonly TicketRow[], overrides: Partial<TicketsState> = {}): TicketsState {
  return { rows, status: 'ready', error: null, ...overrides };
}

describe('selectTicketsView — presentation (rule 2 proof)', () => {
  it('orders rows by lastUpdateAt descending (most recent first), then id for tiebreak', () => {
    const view = selectTicketsView(
      state([
        row({ id: 'T-3', lastUpdateAt: '2026-05-01T00:00:00' }),
        row({ id: 'T-1', lastUpdateAt: '2026-06-01T00:00:00' }),
        row({ id: 'T-4', lastUpdateAt: '2026-04-01T00:00:00' }),
        row({ id: 'T-2', lastUpdateAt: '2026-06-01T00:00:00' }),
      ]),
    );
    // T-1 and T-2 share the same date; T-1 < T-2 alphabetically → T-1 first.
    expect(view.rows.map((r) => r.id)).toEqual(['T-1', 'T-2', 'T-3', 'T-4']);
  });

  it('formats idCell with truncation at ID_WIDTH', () => {
    const longId = `T-${'9'.repeat(20)}`;
    const view = selectTicketsView(state([row({ id: longId })]));
    expect(view.rows[0]?.idCell).toMatch(/…$/); // truncated with ellipsis
    expect(view.rows[0]?.idCell.length).toBeLessThanOrEqual(8);
  });

  it('formats titleCell with truncation at TITLE_WIDTH', () => {
    const longTitle = 'A'.repeat(50);
    const view = selectTicketsView(state([row({ title: longTitle })]));
    expect(view.rows[0]?.titleCell).toMatch(/…$/);
    expect(view.rows[0]?.titleCell.length).toBeLessThanOrEqual(24);
  });

  it('renders statusCell as a glyph (Goal A): ● for in_progress (running)', () => {
    const view = selectTicketsView(state([row({ status: 'in_progress' })]));
    expect(view.rows[0]?.statusCell).toBe('●');
    expect(view.rows[0]?.statusTone).toBe('warning');
  });

  describe('status glyph ladder (Goal A)', () => {
    it('failed → ✗ (error)', () => {
      const v = selectTicketsView(state([row({ status: 'failed' })]));
      expect(v.rows[0]?.statusCell).toBe('✗');
      expect(v.rows[0]?.statusTone).toBe('error');
    });
    it('done → ✓ (success)', () => {
      const v = selectTicketsView(state([row({ status: 'done' })]));
      expect(v.rows[0]?.statusCell).toBe('✓');
      expect(v.rows[0]?.statusTone).toBe('success');
    });
    it('blocked → ⊘ with its OWN blocked tone (not error)', () => {
      const v = selectTicketsView(state([row({ status: 'blocked' })]));
      expect(v.rows[0]?.statusCell).toBe('⊘');
      expect(v.rows[0]?.statusTone).toBe('blocked');
    });
    it('draft and planned both → ◌ (neutral)', () => {
      const draft = selectTicketsView(state([row({ status: 'draft' })]));
      expect(draft.rows[0]?.statusCell).toBe('◌');
      const planned = selectTicketsView(state([row({ status: 'planned' })]));
      expect(planned.rows[0]?.statusCell).toBe('◌');
      expect(planned.rows[0]?.statusTone).toBe('neutral');
    });
    it('ready + unmet deps → ◍ waiting-on-dependency', () => {
      const v = selectTicketsView(state([row({ status: 'ready', pendingDepIds: ['T-9'] })]));
      expect(v.rows[0]?.statusCell).toBe('◍');
    });
    it('ready + future schedule_at → ◷ scheduled', () => {
      const now = Date.parse('2026-06-01T00:00:00Z');
      const v = selectTicketsView(
        state([row({ status: 'ready', scheduleAt: '2026-06-02T00:00:00Z' })]),
        now,
      );
      expect(v.rows[0]?.statusCell).toBe('◷');
    });
    it('ready + deps ok + not future-scheduled → ◕ queued', () => {
      const now = Date.parse('2026-06-01T00:00:00Z');
      const v = selectTicketsView(
        state([row({ status: 'ready', scheduleAt: '2026-05-01T00:00:00Z' })]),
        now,
      );
      expect(v.rows[0]?.statusCell).toBe('◕');
    });
    it('plain ready (no deps, no schedule) → ◕ queued (eligible)', () => {
      const v = selectTicketsView(state([row({ status: 'ready' })]));
      expect(v.rows[0]?.statusCell).toBe('◕');
    });
  });

  describe('subticket tree (Goal B) — mirrors plans', () => {
    it('renders children indented under their parent', () => {
      const view = selectTicketsView(
        state([
          row({
            id: 'T-child',
            title: 'Child',
            parent: 'T-parent',
            lastUpdateAt: '2026-06-02T00:00:00',
          }),
          row({ id: 'T-parent', title: 'Parent', lastUpdateAt: '2026-06-01T00:00:00' }),
        ]),
      );
      expect(view.rows.map((r) => r.id)).toEqual(['T-parent', 'T-child']);
      expect(view.rows[0]?.depth).toBe(0);
      expect(view.rows[0]?.titleCell).toBe('Parent');
      expect(view.rows[1]?.depth).toBe(1);
      expect(view.rows[1]?.titleCell).toBe('    Child'); // 4-space indent
    });
    it('a child naming an unknown parent is treated as top-level (never dropped)', () => {
      const view = selectTicketsView(state([row({ id: 'T-orphan', parent: 'missing' })]));
      expect(view.rows).toHaveLength(1);
      expect(view.rows[0]?.depth).toBe(0);
    });
  });

  it('formats lastUpdateCell as YYYY-MM-DD + label', () => {
    const view = selectTicketsView(
      state([row({ lastUpdateAt: '2026-06-08T14:30:00', lastUpdateLabel: 'done' })]),
    );
    expect(view.rows[0]?.lastUpdateCell).toContain('2026-06-08');
    expect(view.rows[0]?.lastUpdateCell).toContain('done');
  });

  it('formats harnessCell and modelCell separately (col 4)', () => {
    const view = selectTicketsView(
      state([row({ harness: 'claude', model: 'anthropic/claude-opus' })]),
    );
    expect(view.rows[0]?.harnessCell).toBe('claude');
    expect(view.rows[0]?.modelCell).toBe('claude-opus');
    // The provider prefix 'anthropic/' is stripped from modelCell.
    expect(view.rows[0]?.modelCell).not.toContain('anthropic/');
  });

  it('uses "—" for harness/model when absent', () => {
    const view = selectTicketsView(state([row({ harness: null, model: null })]));
    expect(view.rows[0]?.harnessCell).toBe('—');
    expect(view.rows[0]?.modelCell).toBe('—');
  });

  describe('depsCell + depsSatisfied — the pending_dep_ids rendering (col 3)', () => {
    it('renders "ok" and depsSatisfied=true when pendingDepIds is empty (all deps satisfied)', () => {
      const view = selectTicketsView(state([row({ pendingDepIds: [] })]));
      expect(view.rows[0]?.depsCell).toBe('ok');
      expect(view.rows[0]?.depsSatisfied).toBe(true);
    });

    it('joins non-done dep ids with ", " and depsSatisfied=false when there are pending deps', () => {
      const view = selectTicketsView(state([row({ pendingDepIds: ['T-2', 'T-5'] })]));
      expect(view.rows[0]?.depsCell).toBe('T-2, T-5');
      expect(view.rows[0]?.depsSatisfied).toBe(false);
    });

    it('truncates a very long deps list', () => {
      const manyIds = Array.from({ length: 20 }, (_, i) => `T-${i + 10}`);
      const view = selectTicketsView(state([row({ pendingDepIds: manyIds })]));
      // Should be truncated (the raw join of 20 ids would exceed DEPS_WIDTH=24).
      expect(view.rows[0]?.depsCell.length).toBeLessThanOrEqual(24);
      expect(view.rows[0]?.depsSatisfied).toBe(false);
    });
  });

  it('renders scheduleCell from scheduleAt, "—" when null', () => {
    const withSchedule = selectTicketsView(state([row({ scheduleAt: 'Mon 09:00' })]));
    expect(withSchedule.rows[0]?.scheduleCell).toBe('Mon 09:00');

    const noSchedule = selectTicketsView(state([row({ scheduleAt: null })]));
    expect(noSchedule.rows[0]?.scheduleCell).toBe('—');
  });

  it('renders planCell and worktreeCell as "—" (contract gap: not on wire DTO)', () => {
    const view = selectTicketsView(state([row()]));
    expect(view.rows[0]?.planCell).toBe('—');
    expect(view.rows[0]?.worktreeCell).toBe('—');
  });

  it('alternates rowParity: 0 for even-indexed, 1 for odd-indexed (after sort)', () => {
    const view = selectTicketsView(
      state([
        row({ id: 'T-1', lastUpdateAt: '2026-06-03T00:00:00' }),
        row({ id: 'T-2', lastUpdateAt: '2026-06-02T00:00:00' }),
        row({ id: 'T-3', lastUpdateAt: '2026-06-01T00:00:00' }),
      ]),
    );
    // Sorted order: T-1, T-2, T-3
    expect(view.rows[0]?.rowParity).toBe(0); // index 0 → even
    expect(view.rows[1]?.rowParity).toBe(1); // index 1 → odd
    expect(view.rows[2]?.rowParity).toBe(0); // index 2 → even
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectTicketsView(state([])).isEmpty).toBe(true);
    expect(selectTicketsView(state([row()])).isEmpty).toBe(false);
    const err = selectTicketsView(state([], { status: 'error', error: 'oops' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('oops');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ id: 'T-2', lastUpdateAt: '2026-05-01T00:00:00' }),
      row({ id: 'T-1', lastUpdateAt: '2026-06-01T00:00:00' }),
    ];
    const original = [...rows];
    selectTicketsView(state(rows));
    expect(rows).toEqual(original);
  });
});
