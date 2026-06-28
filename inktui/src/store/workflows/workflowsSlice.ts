/**
 * Workflows slice — the registry of named ticket-tree workflows (`:name` leading-fire macros).
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * Like {@link ../templates/templatesSlice.js templates} (its sibling pattern), this is none of the
 * `{ rows, status, error }` snapshot-re-pull shape the list-slice factory serves. The state is a list
 * of {@link WorkflowDef} records plus a load lifecycle, loaded once via `tui.load_workflows` and
 * persisted via `tui.save_workflows` (never snapshot-invalidated). So — like `favorites`, `templates`,
 * and `conversations` — this is a hand-written slice with its own shape.
 *
 * ## What a workflow is
 *
 * A workflow is a `{ name, description, mode, stages }` record: `name` is the `:name` leading-fire key
 * (validated server-side against `^[A-Za-z0-9_-]+$`), and `stages` is the ordered ticket-tree spec the
 * backend materializes when the workflow fires. The canonical list is normalized by the backend on
 * save and echoed back, so a successful save SYNCS the slice to the returned list — the store never
 * holds a list the server would have rejected/reordered.
 *
 * Ref-swap granularity: every mutation replaces the whole `workflows` slice object (and the inner
 * `items` array), so `useAppStore(s => s.workflows, shallow)` subscribers re-render only when the
 * registry actually changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/** One stage of a workflow: a node in the ticket tree the backend materializes on fire. Mirrors the
 * backend stage dict. `depends_on` lists sibling stage ids this stage gates behind. */
export interface WorkflowStageDef {
  readonly id: string;
  readonly title: string;
  readonly instructions: string;
  readonly harness: string;
  readonly model: string;
  readonly worktree: string;
  readonly depends_on: readonly string[];
  readonly gate: string;
}

/** One named workflow: `name` is the `:name` leading-fire key, `stages` the ordered ticket-tree spec
 * the backend materializes when the workflow fires. Mirrors the backend `WorkflowDef` dict. */
export interface WorkflowDef {
  readonly name: string;
  readonly description: string;
  readonly mode: string;
  readonly stages: readonly WorkflowStageDef[];
}

/**
 * The workflows slice state. `items` is the registry (canonical/normalized after a save); `status`
 * makes the initial `tui.load_workflows` lifecycle explicit so a selector/component can tell "not
 * loaded yet" from "loaded, none defined". `error` carries a failed load/save message. All readonly
 * — ref-swapped wholesale on change.
 */
export interface WorkflowsState {
  /** The named workflows. Normalized by the backend after each save. */
  readonly items: readonly WorkflowDef[];
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
}

/** The initial, pre-load slice value. A fresh store has not called `tui.load_workflows` yet. */
export const initialWorkflowsState: WorkflowsState = {
  items: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `workflows` key. Not a
 * `createListSlice` shell (this slice has its own shape); mutation is the action layer's job
 * (rule 3 — see {@link ./workflowsActions.js}). Contributes only the `workflows` key; `../store.ts`
 * composes it.
 */
export const createWorkflowsSlice: StateCreator<
  AppStore,
  [],
  [],
  { workflows: WorkflowsState }
> = () => ({
  workflows: initialWorkflowsState,
});

/**
 * Index the workflows by name into a `Map<string, WorkflowDef>` — the lookup shape the send-path
 * firing code consumes. Last-wins on a duplicate name (the backend normalizes away duplicates, but a
 * pre-save optimistic list could momentarily hold one).
 */
export function selectWorkflowsByName(items: readonly WorkflowDef[]): Map<string, WorkflowDef> {
  const byName = new Map<string, WorkflowDef>();
  for (const item of items) {
    byName.set(item.name, item);
  }
  return byName;
}
