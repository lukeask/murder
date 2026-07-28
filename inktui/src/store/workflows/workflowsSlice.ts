/**
 * Workflows slice — the registry of named ticket-tree workflows (`:name` leading-fire macros).
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * Like {@link ../templates/templatesSlice.js templates} (its sibling pattern), this is none of the
 * `{ rows, status, error }` snapshot-re-pull shape the list-slice factory serves. The state is a list
 * of {@link WorkflowTemplate} records plus a load lifecycle, loaded once via `workflows.get` and
 * persisted via `workflows.set` (never snapshot-invalidated). So — like `favorites`, `templates`,
 * and `conversations` — this is a hand-written slice with its own shape.
 *
 * ## What a workflow template is
 *
 * A workflow template is a `{ name, description, mode, stages }` record: `name` is the `:name`
 * leading-fire key (validated server-side against `^[A-Za-z0-9_-]+$`), and `stages` is the ordered
 * ticket-tree spec the backend materializes when the workflow fires. The canonical list is normalized
 * by the backend on save and echoed back, so a successful save SYNCS the slice to the returned list —
 * the store never holds a list the server would have rejected/reordered.
 *
 * Ref-swap granularity: every mutation replaces the whole `workflows` slice object (and the inner
 * `items` array), so `useAppStore(s => s.workflows, shallow)` subscribers re-render only when the
 * registry actually changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { QueryResult } from '../../application/ApplicationClient.js';
import type { AppStore } from '../store.js';

/** One node of a workflow template: a stage in the ticket tree the backend materializes on fire.
 * Mirrors the backend stage dict. `depends_on` lists sibling stage ids this node gates behind. */
export type WorkflowNodeTemplate = NonNullable<
  QueryResult<'workflows.get'>['workflows'][number]['stages']
>[number];

/** @deprecated Prefer {@link WorkflowNodeTemplate}. Kept for call-site migration. */
export type WorkflowStageDef = WorkflowNodeTemplate;

/** One named workflow template: `name` is the `:name` leading-fire key, `stages` the ordered
 * ticket-tree spec the backend materializes when the workflow fires. Local name for the wire shape
 * that still mirrors the backend `WorkflowDef` dict. */
export type WorkflowTemplate = QueryResult<'workflows.get'>['workflows'][number];

/** @deprecated Prefer {@link WorkflowTemplate}. Kept while protocol/generated names still say Def. */
export type WorkflowDef = WorkflowTemplate;

/**
 * The workflows slice state. `items` is the registry (canonical/normalized after a save); `status`
 * makes the initial `workflows.get` lifecycle explicit so a selector/component can tell "not
 * loaded yet" from "loaded, none defined". `error` carries a failed load/save message. All readonly
 * — ref-swapped wholesale on change.
 */
export interface WorkflowsState {
  /** The named workflow templates. Normalized by the backend after each save. */
  readonly items: readonly WorkflowTemplate[];
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
  /** Opaque server-side registry version used for atomic workflow mutations. */
  readonly revision: string;
}

/** The initial, pre-load slice value. A fresh store has not called `workflows.get` yet. */
export const initialWorkflowsState: WorkflowsState = {
  items: [],
  status: 'idle',
  error: null,
  revision: '',
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
 * Index the workflow templates by name into a `Map<string, WorkflowTemplate>` — the lookup shape the
 * send-path firing code consumes. Last-wins on a duplicate name (the backend normalizes away
 * duplicates, but a pre-save optimistic list could momentarily hold one).
 */
export function selectWorkflowsByName(
  items: readonly WorkflowTemplate[],
): Map<string, WorkflowTemplate> {
  const byName = new Map<string, WorkflowTemplate>();
  for (const item of items) {
    byName.set(item.name, item);
  }
  return byName;
}
