/**
 * Templates slice — the registry of named text-expansion templates (`:name:` macros).
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * The list-slice factory is for `{ rows, status, error }` re-pulled wholesale after projection invalidation
 * entity event. Templates are none of that: the state is a list of `{ name, body }` records plus a
 * load lifecycle, loaded once via `tui.load_templates` and persisted via `tui.save_templates` (never
 * snapshot-invalidated). So — like `favorites` and `conversations` — this is a hand-written slice
 * with its own shape (the documented precedent for a non-factory, non-snapshot slice).
 *
 * ## What a template is
 *
 * A template is a `{ name, body }` pair: `name` is the `:name:` macro key (validated server-side
 * against `^[A-Za-z0-9_-]+$`), `body` the text it expands to. The canonical list is normalized by
 * the backend on save (validate names, de-dupe by name last-wins, sort by name) and echoed back, so
 * a successful save SYNCS the slice to the returned list — the store never holds a list the server
 * would have rejected/reordered.
 *
 * Ref-swap granularity: every mutation replaces the whole `templates` slice object (and the inner
 * `items` array), so `useAppStore(s => s.templates, shallow)` subscribers re-render only when the
 * registry actually changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/** One named template: `name` is the `:name:` macro key, `body` the expansion text. */
export interface TemplateRecord {
  readonly name: string;
  readonly body: string;
}

/**
 * The templates slice state. `items` is the registry (canonical/normalized after a save); `status`
 * makes the initial `tui.load_templates` lifecycle explicit so a selector/component can tell "not
 * loaded yet" from "loaded, none defined". `error` carries a failed load/save message. All readonly
 * — ref-swapped wholesale on change.
 */
export interface TemplatesState {
  /** The named templates. Normalized (sorted, de-duped) by the backend after each save. */
  readonly items: readonly TemplateRecord[];
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
}

/** The initial, pre-load slice value. A fresh store has not called `tui.load_templates` yet. */
export const initialTemplatesState: TemplatesState = {
  items: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `templates` key. Not a
 * `createListSlice` shell (this slice has its own shape); mutation is the action layer's job
 * (rule 3 — see {@link ./templatesActions.js}). Contributes only the `templates` key; `../store.ts`
 * composes it.
 */
export const createTemplatesSlice: StateCreator<
  AppStore,
  [],
  [],
  { templates: TemplatesState }
> = () => ({
  templates: initialTemplatesState,
});

/**
 * Index the templates by name into a `Map<string, TemplateRecord>` — the lookup shape the expansion
 * code and the settings UI consume. Last-wins on a duplicate name (the backend normalizes away
 * duplicates, but a pre-save optimistic list could momentarily hold one).
 */
export function selectTemplatesByName(
  items: readonly TemplateRecord[],
): Map<string, TemplateRecord> {
  const byName = new Map<string, TemplateRecord>();
  for (const item of items) {
    byName.set(item.name, item);
  }
  return byName;
}
