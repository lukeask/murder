/**
 * Favorites selectors — the rule-2 home of "is this favorited?" and "sort starred to the top".
 *
 * Starring is generalized across panels (plans/notes/reports/crows), so the *predicate* and the
 * *sort* both live here once, not re-implemented per panel. A panel selector calls {@link stableSortStarredFirst}
 * with the favorite set; a crow selector calls {@link isFavorited} (which also honours the
 * default-favorited rule). Pure — no React, no store, no bus (callable from a future DOM client).
 *
 * ## The two favorite sources, unified
 *
 * Per the spec, a crow can be favorited two ways:
 *  1. *explicitly*, by the user pressing `ctrl+s` on it — persisted in the favorites slice's `ids`.
 *  2. *by default*, because of its kind (collaborator always; rogue crows on creation) — derived,
 *     never persisted (see {@link ./agentIdentity.js isDefaultFavorited} and the favorites slice doc).
 * {@link isFavorited} ORs the two so a caller asks one question. Docs (plans/notes/reports) have no
 * default-favorited concept, so for them "favorited" is purely set membership — callers pass
 * `defaultFavorited: false` (or use {@link isInFavoriteSet} directly).
 */

import { useMemo } from 'react';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';

/** Whether `id` is in the explicit (persisted) favorite set. The only check docs need. */
export function isInFavoriteSet(favorites: FavoritesState, id: string): boolean {
  return favorites.ids.has(id);
}

/**
 * Whether an item is favorited, ORing the explicit persisted set with a derived default. `id` is
 * the item's favorite id (filename for docs, agentId for crows); `defaultFavorited` is the
 * kind-derived default (always `false` for docs; for crows pass
 * `isDefaultFavorited(identity)`). This is the one predicate every "show the star / show the
 * history pane" decision goes through.
 */
export function isFavorited(
  favorites: FavoritesState,
  id: string,
  defaultFavorited: boolean,
): boolean {
  return defaultFavorited || favorites.ids.has(id);
}

/**
 * Stable-sort a copy of `rows` so favorited rows come first, preserving the input order within each
 * group (favorited block keeps its order; non-favorited block keeps its order). This is the
 * "starred shown at top" rule (spec › Starring), applied as a *stable* re-partition on top of
 * whatever domain order the caller already produced — so a panel's existing sort (recency,
 * parent-tree, status) is preserved within the starred and unstarred groups.
 *
 * @param rows  already in the caller's domain order.
 * @param idOf  extract a row's favorite id.
 * @param isFav decide if a row is favorited (the caller closes over the favorite set + any default).
 */
export function stableSortStarredFirst<Row>(
  rows: readonly Row[],
  idOf: (row: Row) => string,
  isFav: (id: string) => boolean,
): Row[] {
  const starred: Row[] = [];
  const rest: Row[] = [];
  for (const row of rows) {
    if (isFav(idOf(row))) {
      starred.push(row);
    } else {
      rest.push(row);
    }
  }
  return [...starred, ...rest];
}

/**
 * Component-facing hook: memoise a favorite-id-set membership closure on the slice identity. A panel
 * selector that needs `isFav` for {@link stableSortStarredFirst} can build it from this. Re-runs
 * only when the favorites slice ref-changes (the granularity contract).
 */
export function useFavoritePredicate(favorites: FavoritesState): (id: string) => boolean {
  return useMemo(() => (id: string) => favorites.ids.has(id), [favorites]);
}
