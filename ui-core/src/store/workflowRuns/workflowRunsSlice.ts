import type { StateCreator } from 'zustand';
import type { QueryResult } from '../../application/ApplicationClient.js';
import type { AppStore } from '../store.js';

/** One run from `workflow.runs.get` (detail) or an equivalent list entry. */
export type WorkflowRun = NonNullable<QueryResult<'workflow.runs.get'>['run']>;

/** One entry from `workflow.runs.list` (same wire shape as a get-able run). */
export type WorkflowRunListItem = QueryResult<'workflow.runs.list'>['runs'][number];

export type WorkflowRunStatus = WorkflowRunListItem['status'];

export interface WorkflowRunsState {
  /** Authoritative list of known runs for the Workflows panel. */
  readonly runs: readonly WorkflowRunListItem[];
  readonly listStatus: 'idle' | 'loading' | 'ready' | 'error';
  readonly listError: string | null;
  /** Optional single-run monitor (workflow editor / follow-up detail). */
  readonly activeWorkflowId: string | null;
  readonly activeRun: WorkflowRun | null;
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly error: string | null;
}

export const initialWorkflowRunsState: WorkflowRunsState = {
  runs: [],
  listStatus: 'idle',
  listError: null,
  activeWorkflowId: null,
  activeRun: null,
  status: 'idle',
  error: null,
};

export const createWorkflowRunsSlice: StateCreator<
  AppStore,
  [],
  [],
  { workflowRuns: WorkflowRunsState }
> = () => ({
  workflowRuns: initialWorkflowRunsState,
});
