/**
 * Settings slice — the user's persisted TUI preferences (command modifier, theme, key rebinds),
 * the backbone of the settings menu (Phase 5) and the source the input/theme stores bridge from.
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * Like {@link ../favorites/favoritesSlice.js favorites}, this is none of the list-slice factory's
 * `{ rows, status, error }` shape re-pulled on a `state.snapshot`. Settings are a small fixed record
 * loaded once via `settings.get` and persisted via `settings.update` (never snapshot-invalidated). So
 * it is a hand-written slice with its own shape — the documented precedent for a non-factory,
 * non-snapshot slice (see the favorites module doc).
 *
 * ## Shape mirrors the Python `TuiUserConfig`
 *
 * `modifier` / `theme` / `keyOverrides` are exactly the `settings.{get,update}` wire fields
 * (snake_case `key_overrides` on the wire → camelCase `keyOverrides` here). The frontend's binding
 * registry (`src/input/bindings.ts`) is the authority on `ActionId`s; the slice stores `keyOverrides`
 * opaquely as a `Record<string,string>` (the server does too) — a bridge narrows it onto the
 * bindings store, which knows the `ActionId` union.
 *
 * Ref-swap granularity: every mutation replaces the whole `settings` slice object, so
 * `useAppStore(s => s.settings, shallow)` subscribers re-render only when a preference actually
 * changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/** The command modifier the user has chosen. Mirrors `bindings.ts`'s `Modifier` (kept structural
 * here so the slice does not depend on the input layer — the bridge couples them). */
export type SettingsModifier = 'alt' | 'ctrl' | 'both';

/**
 * The settings slice state. `theme`/`modifier`/`keyOverrides` are the persisted preferences;
 * `status` makes the initial `settings.get` lifecycle explicit (so a view can tell "not loaded yet"
 * from "loaded at defaults"); `error` carries a failed load/save message. All readonly — ref-swapped
 * wholesale on change.
 */
export interface SettingsState {
  /** The selected theme id. Defaults to the hard Everforest Dark build (matches the Python default). */
  readonly theme: string;
  /** The command modifier (alt / ctrl / both). Bridged onto the bindings store. */
  readonly modifier: SettingsModifier;
  /** Per-action key-char rebinds, opaque `ActionId -> key char`. Bridged onto the bindings store. */
  readonly keyOverrides: Readonly<Record<string, string>>;
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
}

/** The initial, pre-load slice value — the defaults that mirror the Python `TuiUserConfig` defaults
 * (so the UI looks identical before `settings.get` resolves and after, when nothing is persisted). */
export const initialSettingsState: SettingsState = {
  theme: 'everforest-dark',
  modifier: 'alt',
  keyOverrides: {},
  status: 'idle',
  error: null,
};

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `settings` key. Not a
 * `createListSlice` shell (this slice has its own shape); mutation is the action layer's job
 * (rule 3 — see {@link ./settingsActions.js}). Contributes only the `settings` key; `../store.ts`
 * composes it.
 */
export const createSettingsSlice: StateCreator<
  AppStore,
  [],
  [],
  { settings: SettingsState }
> = () => ({
  settings: initialSettingsState,
});
