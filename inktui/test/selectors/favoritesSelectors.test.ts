/**
 * Favorites selector tests — the shared `isFavorited` predicate + `stableSortStarredFirst` partition
 * that every panel reuses (rule 2). Pure functions; no store/React.
 */

import { describe, expect, it } from 'vitest';
import {
  isFavorited,
  isInFavoriteSet,
  stableSortStarredFirst,
} from '../../src/selectors/favoritesSelectors.js';
import type { FavoritesState } from '../../src/store/favorites/favoritesSlice.js';

function favs(ids: readonly string[] = []): FavoritesState {
  return { ids: new Set(ids), status: 'ready', error: null };
}

describe('isInFavoriteSet / isFavorited', () => {
  it('isInFavoriteSet is pure set membership', () => {
    expect(isInFavoriteSet(favs(['a']), 'a')).toBe(true);
    expect(isInFavoriteSet(favs(['a']), 'b')).toBe(false);
  });

  it('isFavorited ORs the explicit set with the kind-derived default', () => {
    // default-favorited (e.g. a collaborator) is favorited even when not in the set.
    expect(isFavorited(favs([]), 'collab', true)).toBe(true);
    // not default → only favorited if explicitly starred.
    expect(isFavorited(favs([]), 'planner-1', false)).toBe(false);
    expect(isFavorited(favs(['planner-1']), 'planner-1', false)).toBe(true);
  });
});

describe('stableSortStarredFirst', () => {
  it('floats favorited rows to the top, preserving input order within each block', () => {
    const rows = [
      { id: 'a', fav: false },
      { id: 'b', fav: true },
      { id: 'c', fav: false },
      { id: 'd', fav: true },
    ];
    const out = stableSortStarredFirst(
      rows,
      (r) => r.id,
      (id) => rows.find((r) => r.id === id)?.fav === true,
    );
    // starred (b, d in input order) then unstarred (a, c in input order).
    expect(out.map((r) => r.id)).toEqual(['b', 'd', 'a', 'c']);
  });

  it('returns a new array (does not mutate the input)', () => {
    const rows = [{ id: 'a' }, { id: 'b' }];
    const original = [...rows];
    stableSortStarredFirst(
      rows,
      (r) => r.id,
      () => false,
    );
    expect(rows).toEqual(original);
  });
});
