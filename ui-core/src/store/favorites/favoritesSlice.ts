/**
 * Favorites slice — the set of starred item ids, the backbone of generalized starring (C11).
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * The list-slice factory is for `{ rows, status, error }` re-pulled wholesale after projection invalidation
 * entity event. Favorites are none of that: the state is a `Set<string>` of starred ids plus a load
 * lifecycle, loaded once via `tui.load_favorites` and persisted via `tui.save_favorites` (never
 * snapshot-invalidated). So — like `conversations` and `ticketDetail` — this is a hand-written slice
 * with its own shape (the documented precedent for a non-factory, non-snapshot slice).
 *
 * ## What an "id" is
 *
 * A favorite id is the stable identity of a starrable thing across the panels that can star:
 *  - plan / note / report → its filename (`row.name`), the same value the panel keys rows by.
 *  - crow → its `agentId` (the discriminated-union identity's routing key).
 * These id spaces don't collide in practice (filenames vs. agent ids), and the prefs RPC persists a
 * flat id list, so one `Set<string>` is the honest shape. A panel resolves its highlighted row to an
 * id and toggles it; a selector reads the set to sort starred-to-top. The slice never inspects what
 * *kind* of thing an id names — that stays the caller's/selector's job (rule 2).
 *
 * ## Defaults vs. persisted prefs
 *
 * Some items are favorited *by default* with no user action (collaborator always; rogue crows on
 * creation — see {@link ../../selectors/agentIdentity.js isDefaultFavorited}). Those defaults are
 * NOT stored here: they are derived from the roster, because a default is a property of the agent's
 * kind, not a persisted user choice. This slice holds only the *explicit, persisted* favorite ids.
 * The "is this favorited?" question for crows therefore ORs the two — see
 * {@link ../../selectors/favoritesSelectors.js isFavorited}. Keeping defaults out of the persisted
 * set means starring/unstarring a default-favorited crow does not have to first materialise the
 * default into the set; the set stays a faithful record of explicit choices, exactly what the prefs
 * RPC round-trips.
 *
 * Ref-swap granularity: every mutation replaces the whole `favorites` slice object (and the inner
 * `ids` Set), so `useAppStore(s => s.favorites, shallow)` subscribers re-render only when the set
 * actually changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/**
 * The favorites slice state. `ids` is the set of *explicitly* starred item ids (filenames for
 * docs, agent ids for crows); `status` makes the initial `tui.load_favorites` lifecycle explicit so
 * a selector/component can tell "not loaded yet" from "loaded, none starred". `error` carries a
 * failed load/save message. All readonly — ref-swapped wholesale on change.
 */
export interface FavoritesState {
  /** The explicitly-starred ids. Default-favorited crows are NOT in here (see the module doc). */
  readonly ids: ReadonlySet<string>;
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
}

/** The initial, pre-load slice value. A fresh store has not called `tui.load_favorites` yet. */
export const initialFavoritesState: FavoritesState = {
  ids: new Set<string>(),
  status: 'idle',
  error: null,
};

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `favorites` key. Not a
 * `createListSlice` shell (this slice has its own shape); mutation is the action layer's job
 * (rule 3 — see {@link ./favoritesActions.js}). Contributes only the `favorites` key; `../store.ts`
 * composes it.
 */
export const createFavoritesSlice: StateCreator<
  AppStore,
  [],
  [],
  { favorites: FavoritesState }
> = () => ({
  favorites: initialFavoritesState,
});
