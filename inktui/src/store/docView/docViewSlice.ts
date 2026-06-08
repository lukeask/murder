/**
 * Doc-view slice — the currently-open read-only document (plan / note / report) shown in the TUI.
 *
 * `enter` on a highlighted plan/note/report toggles a read-only doc view in the layout (spec ›
 * Starring & document toggling). This slice holds *which* doc is open and its fetched body. It is
 * the on-demand-detail precedent {@link ../ticketDetail/ticketDetailSlice.js} established (a
 * hand-written, non-snapshot-invalidated slice loaded when the user opens something), but read-only:
 * there is no edit buffer, no save, no frontmatter — just kind + name + body + load lifecycle.
 *
 * ## Also: the "focused doc" the spawn wizard reads
 *
 * The doc-view IS the "focused doc" the `ctrl+s` spawn-context step references (spec › Keybinds:
 * "focused-doc-wins — list row or opened doc widget alike"). `App.tsx`'s `deriveSpawnContext` reads
 * `open` to build the reference-by-path context, replacing C13's first-row proxy. So an *opened* doc
 * is the focused doc — the cleanest reading of "last-focused doc" that keeps panel cursors local
 * (rule 1): nothing has to lift a cursor into a shared store, because the open doc is already shared
 * state with a real identity (`{ kind, name }`).
 *
 * `kind` + `name` together give the `.murder/<dir>/<name>.md` path the spawn wizard needs and the
 * fetch RPC the body comes from. `open: null` = no doc shown (the layout returns to its panels).
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/** Which list a doc came from — selects both the fetch RPC and the `.murder/<dir>/` path segment. */
export type DocKind = 'plan' | 'note' | 'report';

/** The directory under `.murder/` for each doc kind — the path segment for reference-by-path. */
export const DOC_DIR: Readonly<Record<DocKind, string>> = {
  plan: 'plans',
  note: 'notes',
  report: 'reports',
};

/** Identifies the open doc: its kind + filename (no extension). The `.murder/<dir>/<name>.md` path
 * and the favorite id both derive from this. */
export interface OpenDoc {
  readonly kind: DocKind;
  readonly name: string;
}

/**
 * The doc-view slice state. `open: null` = no doc shown. When non-null, `body` is the fetched
 * markdown (or `null` while loading), and `status` drives the loading/error chrome. All readonly —
 * ref-swapped wholesale on change.
 */
export interface DocViewState {
  /** The doc currently shown, or `null` when the view is closed (panels visible normally). */
  readonly open: OpenDoc | null;
  /** The fetched markdown body. `null` while loading / on error / when closed. */
  readonly body: string | null;
  /** Load lifecycle for the open doc. `idle` when closed. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last fetch rejected; cleared on the next open. */
  readonly error: string | null;
}

/** Initial (closed) state — no doc open. */
export const initialDocViewState: DocViewState = {
  open: null,
  body: null,
  status: 'idle',
  error: null,
};

/**
 * Slice factory. Not a `createListSlice` shell — this slice has its own shape (on-demand single doc,
 * not a `{rows,status,error}` list re-pulled on a snapshot). Contributes only the `docView` key;
 * `../store.ts` composes it. Mutation is the action layer's job (rule 3).
 */
export const createDocViewSlice: StateCreator<
  AppStore,
  [],
  [],
  { docView: DocViewState }
> = () => ({
  docView: initialDocViewState,
});
