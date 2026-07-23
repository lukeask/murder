/**
 * Spawn-favorites actions — the *only* code that calls the bus for the spawn wizard's saved
 * favorites (rule 3). A "spawn favorite" is a named, reusable bundle of the wizard's first-step
 * choices (harness/model/effort) so a user can re-spawn a familiar setup in one pick.
 *
 * Two RPCs, mirroring the favorites prefs pair (both LIVE — registered in `host.py`):
 *  - `tui.load_spawn_favorites {}` → `{ ok, favorites: [SpawnFavorite,…] }` — load the saved list.
 *  - `tui.save_spawn_favorites { favorites }` → `{ ok, favorites }` — persist it.
 * Declared via a `declare module` augmentation of the shared {@link RpcMethods} registry, so the
 * frozen C1/C2 bus files (`ApplicationClient.ts`/`ApplicationWebSocketClient.ts`) stay byte-identical — the seam (rule 4).
 * The keys here (`tui.load_spawn_favorites`/`tui.save_spawn_favorites`) are distinct from every
 * other slice's keys.
 *
 * ## Application protocol status: live
 *
 * Both methods are registered on the live Python bus (`host.py`). Spawn favorites persist
 * user-level at `~/.config/murder/spawn_favorites.yaml` (NOT in `.murder/`, so they follow the
 * user across projects).
 *
 * ## Load vs save error policy (intentional asymmetry)
 *
 * - `load()` swallows ANY rejection and resolves with `[]` (like {@link createHarnessModelsActions}'s
 *   `fetch` — opening the wizard must never fail just because favorites couldn't be read).
 * - `save()` lets a rejection PROPAGATE so the wizard's save handler can catch it and toast the
 *   failure (a save the user explicitly requested deserves visible feedback).
 */

import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { asCommandResult, asQueryResult } from '../../application/resultCast.js';

/** A named bundle of the wizard's first-step choices, re-spawnable in one pick. */
export interface SpawnFavorite {
  readonly name: string;
  readonly harness: string;
  readonly model: string;
  readonly effort: string;
}

/**
 * The spawn-favorites RPC declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files (rule 4 — the seam). Keys distinct from every other slice's.
 *
 * Both operations are registered in `host.py`. The list round-trips in both directions; the
 * save reply echoes the persisted list back.
 */


/** The spawn-favorites actions, bound to one {@link ApplicationClient}. Wizard-local; no store handle. */
export interface SpawnFavoritesActions {
  /**
   * Load the persisted spawn favorites via `tui.load_spawn_favorites`. Resolves with the saved list
   * on success, or `[]` on ANY rejection (RPC error / transport) — never throws past the action, so
   * opening the wizard is robust.
   */
  load(): Promise<SpawnFavorite[]>;
  /**
   * Persist the given list via `tui.save_spawn_favorites` and resolve with the echoed-back list. A
   * rejection PROPAGATES (unlike {@link load}) so the wizard can toast a failed user-requested save.
   */
  save(favorites: readonly SpawnFavorite[]): Promise<SpawnFavorite[]>;
}

/**
 * Build the spawn-favorites actions bound to one injected {@link ApplicationClient}. No store handle: the
 * favorites list is wizard-local closure state, not a global slice.
 */
export function createSpawnFavoritesActions(bus: ApplicationClient): SpawnFavoritesActions {
  return {
    async load(): Promise<SpawnFavorite[]> {
      try {
        const reply = await bus.query('spawn_favorites.get', {});
        // Coerce the readonly wire list to a mutable array for the caller.
        return [
          ...asQueryResult<'spawn_favorites.get', { favorites: readonly SpawnFavorite[] }>(reply)
            .favorites,
        ];
      } catch {
        // RPC / transport error — opening the wizard must not fail; degrade to no favorites.
        return [];
      }
    },

    async save(favorites: readonly SpawnFavorite[]): Promise<SpawnFavorite[]> {
      // Let a rejection propagate — the wizard catches it to toast (intentional vs `load`).
      const reply = await bus.command('spawn_favorites.set', { favorites });
      return [
        ...asCommandResult<'spawn_favorites.set', { favorites: readonly SpawnFavorite[] }>(reply)
          .favorites,
      ];
    },
  };
}
