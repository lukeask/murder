/**
 * Notes selector tests — the view-model is a pure function of the slice (rule 2).
 * Copied from {@link ./rosterSelectors.test.ts}. No store, no bus, no React.
 */

import { selectNotesView } from '../../src/selectors/notesSelectors.js';
import type { FavoritesState } from '../../src/store/favorites/favoritesSlice.js';
import type { NoteRow, NotesState } from '../../src/store/notes/notesSlice.js';

function row(overrides: Partial<NoteRow> = {}): NoteRow {
  return {
    name: 'note-alpha',
    charCount: 1234,
    updatedAt: '2026-06-01T10:00:00',
    ...overrides,
  };
}

function state(rows: readonly NoteRow[], overrides: Partial<NotesState> = {}): NotesState {
  return { rows, status: 'ready', error: null, ...overrides };
}

/** Favorites helper for the selector's second arg. */
function favs(ids: readonly string[] = []): FavoritesState {
  return { ids: new Set(ids), status: 'ready', error: null };
}

const NO_FAVS = favs();

describe('selectNotesView — presentation', () => {
  it('orders rows by updatedAt descending (most recent first), then name', () => {
    const view = selectNotesView(
      state([
        row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
        row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
        row({ name: 'd', updatedAt: '2026-04-01T00:00:00' }),
        row({ name: 'c', updatedAt: '2026-06-01T00:00:00' }),
      ]),
      NO_FAVS,
    );
    // 'a' and 'c' share the same date; 'a' < 'c' alphabetically → 'a' first.
    expect(view.rows.map((r) => r.name)).toEqual(['a', 'c', 'b', 'd']);
  });

  it('formats updatedAt as YYYY-MM-DD HH:MM (T replaced with space, 16 chars)', () => {
    const view = selectNotesView(state([row({ updatedAt: '2026-06-08T14:30:00.123' })]), NO_FAVS);
    expect(view.rows[0]?.updatedAt).toBe('2026-06-08 14:30');
  });

  it('formats charCount with a locale-formatted number and "chars" suffix', () => {
    const view = selectNotesView(state([row({ charCount: 1234 })]), NO_FAVS);
    // toLocaleString() varies by locale; just assert it contains "chars" and the digits.
    expect(view.rows[0]?.charCount).toContain('chars');
    expect(view.rows[0]?.charCount).toMatch(/1[,.]?234/);
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectNotesView(state([]), NO_FAVS).isEmpty).toBe(true);
    expect(selectNotesView(state([row()]), NO_FAVS).isEmpty).toBe(false);
    const err = selectNotesView(state([], { status: 'error', error: 'oops' }), NO_FAVS);
    expect(err.status).toBe('error');
    expect(err.error).toBe('oops');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
      row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
    ];
    const original = [...rows];
    selectNotesView(state(rows), NO_FAVS);
    expect(rows).toEqual(original);
  });

  it('floats starred notes to the top, preserving recency within each block (rule 2)', () => {
    const view = selectNotesView(
      state([
        row({ name: 'recent-unstarred', updatedAt: '2026-06-07T00:00:00' }),
        row({ name: 'old-starred', updatedAt: '2026-01-01T00:00:00' }),
        row({ name: 'mid-unstarred', updatedAt: '2026-03-01T00:00:00' }),
      ]),
      favs(['old-starred']),
    );
    // starred first (even though oldest), then unstarred by recency.
    expect(view.rows.map((r) => r.name)).toEqual([
      'old-starred',
      'recent-unstarred',
      'mid-unstarred',
    ]);
    expect(view.rows[0]?.starred).toBe(true);
    expect(view.rows[1]?.starred).toBe(false);
  });
});
