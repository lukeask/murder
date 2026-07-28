import type { StateCreator } from 'zustand';
import type { QueryResult } from '../../application/ApplicationClient.js';
import type { AppStore } from '../store.js';

export type WorkflowRun = NonNullable<QueryResult<'workflow.runs.get'>['run']>;

export interface WorkflowRunsState {
  readonly activeWorkflowId: string | null;
  readonly activeRun: WorkflowRun | null;
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly error: string | null;
}

export const initialWorkflowRunsState: WorkflowRunsState = {
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
