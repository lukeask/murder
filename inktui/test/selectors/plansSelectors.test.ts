/**
 * Plans selector tests — the hardest view-model: parent-tree + 4-space indent, child-recency
 * bubbling the parent's order, and starred-to-top, all reconciled in one precedence (rule 2).
 */

import { describe, expect, it } from 'vitest';
import { selectPlansView } from '../../src/selectors/plansSelectors.js';
import type { FavoritesState } from '../../src/store/favorites/favoritesSlice.js';
import type { PlanRow, PlansState } from '../../src/store/plans/plansSlice.js';

function row(overrides: Partial<PlanRow> = {}): PlanRow {
  return {
    name: 'plan-alpha',
    charCount: 100,
    updatedAt: '2026-06-01T10:00:00',
    parent: null,
    ...overrides,
  };
}

function state(rows: readonly PlanRow[], overrides: Partial<PlansState> = {}): PlansState {
  return { rows, status: 'ready', error: null, ...overrides };
}

function favs(ids: readonly string[] = []): FavoritesState {
  return { ids: new Set(ids), status: 'ready', error: null };
}

const NO_FAVS = favs();

describe('selectPlansView — parent indentation', () => {
  it('lists children directly under their parent, name indented 4 spaces', () => {
    const view = selectPlansView(
      state([
        row({ name: 'parent', updatedAt: '2026-06-01T00:00:00' }),
        row({ name: 'child', parent: 'parent', updatedAt: '2026-05-01T00:00:00' }),
      ]),
      NO_FAVS,
    );
    expect(view.rows.map((r) => r.name)).toEqual(['parent', '    child']);
    expect(view.rows[0]?.depth).toBe(0);
    expect(view.rows[1]?.depth).toBe(1);
    // The stable id is the un-indented filename (what star/open act on).
    expect(view.rows[1]?.id).toBe('child');
  });

  it("bubbles the parent's ordering position by a child's more-recent update", () => {
    // group A: parent old, but a very recent child → effective recency is recent → A sorts first.
    // group B: parent moderately recent, no children.
    const view = selectPlansView(
      state([
        row({ name: 'A-parent', updatedAt: '2026-01-01T00:00:00' }),
        row({ name: 'A-child', parent: 'A-parent', updatedAt: '2026-06-08T00:00:00' }),
        row({ name: 'B-parent', updatedAt: '2026-03-01T00:00:00' }),
      ]),
      NO_FAVS,
    );
    // A's group bubbles to the top because A-child is the most recent update overall.
    expect(view.rows.map((r) => r.name)).toEqual(['A-parent', '    A-child', 'B-parent']);
  });

  it('treats a child naming an unknown parent as top-level (never drops a row)', () => {
    const view = selectPlansView(
      state([row({ name: 'orphan', parent: 'missing', updatedAt: '2026-06-01T00:00:00' })]),
      NO_FAVS,
    );
    expect(view.rows.map((r) => r.name)).toEqual(['orphan']);
    expect(view.rows[0]?.depth).toBe(0);
  });
});

describe('selectPlansView — starring reconciliation', () => {
  it('floats a starred parent group to the top, keeping the subtree together', () => {
    const view = selectPlansView(
      state([
        row({ name: 'recent-unstarred', updatedAt: '2026-06-08T00:00:00' }),
        row({ name: 'old-starred', updatedAt: '2026-01-01T00:00:00' }),
        row({ name: 'kid', parent: 'old-starred', updatedAt: '2026-01-02T00:00:00' }),
      ]),
      favs(['old-starred']),
    );
    // starred parent group first (parent then its child), then the unstarred (more recent) group.
    expect(view.rows.map((r) => r.name)).toEqual(['old-starred', '    kid', 'recent-unstarred']);
    expect(view.rows[0]?.starred).toBe(true);
  });

  it('floats a starred child within its parent subtree (cannot leave the parent)', () => {
    const view = selectPlansView(
      state([
        row({ name: 'parent', updatedAt: '2026-06-01T00:00:00' }),
        row({ name: 'plain-kid', parent: 'parent', updatedAt: '2026-06-05T00:00:00' }),
        row({ name: 'starred-kid', parent: 'parent', updatedAt: '2026-01-01T00:00:00' }),
      ]),
      favs(['starred-kid']),
    );
    // parent first; within the subtree the starred child floats above the more-recent plain child.
    expect(view.rows.map((r) => r.name)).toEqual(['parent', '    starred-kid', '    plain-kid']);
  });
});

describe('selectPlansView — load flags', () => {
  it('carries status/error and computes isEmpty', () => {
    expect(selectPlansView(state([]), NO_FAVS).isEmpty).toBe(true);
    expect(selectPlansView(state([], { status: 'idle' }), NO_FAVS).status).toBe('ready');
    expect(selectPlansView(state([], { status: 'loading' }), NO_FAVS).status).toBe('loading');
    const err = selectPlansView(state([], { status: 'error', error: 'x' }), NO_FAVS);
    expect(err.status).toBe('error');
    expect(err.error).toBe('x');
  });

  it('does not mutate the input slice', () => {
    const rows = [
      row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
      row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
    ];
    const original = [...rows];
    selectPlansView(state(rows), NO_FAVS);
    expect(rows).toEqual(original);
  });
});
