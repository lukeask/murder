import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import type { AppStore } from '../store.js';

export interface WorkflowRunsActions {
  setActive(workflowId: string | null): Promise<void>;
  refresh(): Promise<void>;
}

export function createWorkflowRunsActions(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
): WorkflowRunsActions {
  let requestToken = 0;

  async function refresh(): Promise<void> {
    const workflowId = store.getState().workflowRuns.activeWorkflowId;
    if (workflowId === null) {
      return;
    }
    const token = ++requestToken;
    store.setState((state) => ({
      workflowRuns: { ...state.workflowRuns, status: 'loading', error: null },
    }));
    try {
      const result = await bus.query('workflow.runs.get', {
        workflow_id: workflowId,
        include_waits: false,
      });
      if (token !== requestToken || store.getState().workflowRuns.activeWorkflowId !== workflowId) {
        return;
      }
      if (!result.ok || result.run == null) {
        store.setState({
          workflowRuns: {
            activeWorkflowId: workflowId,
            activeRun: null,
            status: 'error',
            error: result.error ?? 'workflow run not found',
          },
        });
        return;
      }
      store.setState({
        workflowRuns: {
          activeWorkflowId: workflowId,
          activeRun: result.run,
          status: 'ready',
          error: null,
        },
      });
    } catch (error: unknown) {
      if (token !== requestToken || store.getState().workflowRuns.activeWorkflowId !== workflowId) {
        return;
      }
      store.setState((state) => ({
        workflowRuns: {
          ...state.workflowRuns,
          status: 'error',
          error: error instanceof Error ? error.message : String(error),
        },
      }));
    }
  }

  return {
    async setActive(workflowId): Promise<void> {
      ++requestToken;
      store.setState({
        workflowRuns: {
          activeWorkflowId: workflowId,
          activeRun: null,
          status: workflowId === null ? 'idle' : 'loading',
          error: null,
        },
      });
      if (workflowId !== null) {
        await refresh();
      }
    },
    refresh,
  };
}
