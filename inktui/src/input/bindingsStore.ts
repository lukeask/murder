/**
 * `bindingsStore` — the live binding configuration: the user's command-modifier choice, whether
 * ctrl is deliverable by the terminal, the per-action key overrides, and the {@link ResolvedBindings}
 * derived from all three.
 *
 * The store keeps the *inputs* (modifier / ctrlAvailable / overrides) as the source of truth and
 * recomputes the derived `resolved` table whenever any input changes — so subscribers (the
 * dispatcher's wiring hook, the panels, the hint bar) read `resolved` and re-derive only when
 * settings actually change, never per render. The recompute swaps in a fresh `resolved` object, so
 * its identity is a safe `useMemo`/effect dependency (panels re-register their keymaps only on a real
 * settings change).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React here. The React hook (`useBindings`) lives in
 * {@link ../hooks/useInputStores.js}. A future settings phase mutates this store from the settings
 * RPC bridge; Phase 1 leaves it at the defaults (alt, ctrl unavailable, no overrides) so behavior is
 * unchanged.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import {
  type ActionId,
  type Modifier,
  type ResolvedBindings,
  resolveBindings,
} from './bindings.js';

/** The binding store's state: the three configuration inputs, the derived resolved table, and the
 * verbs that update the inputs (each recomputes `resolved`). */
export interface BindingsState {
  /** The user's chosen command modifier. Defaults to `alt` (today's behavior). */
  readonly modifier: Modifier;
  /** Whether the terminal can deliver ctrl chords (kitty protocol). Defaults to `false` until a
   * later phase's detection sets it; until then `ctrl`/`both` degrade to alt. */
  readonly ctrlAvailable: boolean;
  /** Per-action key-char overrides for `command` actions (the settings menu's rebinds). */
  readonly overrides: Readonly<Partial<Record<ActionId, string>>>;
  /** The resolved binding table derived from the three inputs above. Replaced on every change. */
  readonly resolved: ResolvedBindings;
  /** Set the command modifier and recompute `resolved`. */
  setModifier(modifier: Modifier): void;
  /** Set ctrl availability and recompute `resolved`. */
  setCtrlAvailable(ctrlAvailable: boolean): void;
  /** Replace the override map and recompute `resolved`. */
  setOverrides(overrides: Partial<Record<ActionId, string>>): void;
}

/** The handle type, re-exported so callers don't import `zustand/vanilla`. */
export type BindingsStoreApi = StoreApi<BindingsState>;

/** What the store is seeded with. Defaults reproduce today's behavior (alt, no ctrl, no rebinds). */
export interface BindingsInit {
  readonly modifier?: Modifier;
  readonly ctrlAvailable?: boolean;
  readonly overrides?: Partial<Record<ActionId, string>>;
}

/**
 * Create a bindings store. Seeds the three inputs (defaulting to today's behavior) and computes the
 * initial `resolved` table. Each setter recomputes `resolved` from the updated inputs so the derived
 * table never drifts from its inputs.
 */
export function createBindingsStore(init: BindingsInit = {}): BindingsStoreApi {
  const modifier = init.modifier ?? 'alt';
  const ctrlAvailable = init.ctrlAvailable ?? false;
  const overrides = init.overrides ?? {};

  return createStore<BindingsState>()((set) => ({
    modifier,
    ctrlAvailable,
    overrides,
    resolved: resolveBindings(modifier, ctrlAvailable, overrides),
    setModifier(next) {
      set((state) => ({
        modifier: next,
        resolved: resolveBindings(next, state.ctrlAvailable, state.overrides),
      }));
    },
    setCtrlAvailable(next) {
      set((state) => ({
        ctrlAvailable: next,
        resolved: resolveBindings(state.modifier, next, state.overrides),
      }));
    },
    setOverrides(next) {
      set((state) => ({
        overrides: next,
        resolved: resolveBindings(state.modifier, state.ctrlAvailable, next),
      }));
    },
  }));
}
