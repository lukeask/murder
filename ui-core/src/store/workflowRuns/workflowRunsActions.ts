import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import type { AppStore } from '../store.js';

export interface WorkflowRunsActions {
  /** Fetch / refresh the run list used by the Workflows panel. */
  refreshList(): Promise<void>;
  /** Pin a single run for detail monitors (editor); clears when `null`. */
  setActive(workflowId: string | null): Promise<void>;
  /** Refresh the active run (no-op when none is pinned). */
  refreshActive(): Promise<void>;
  /**
   * Refresh list always; also refresh the pinned active run when set.
   * Used by `workflow_runs` projection invalidations.
   */
  refresh(): Promise<void>;
}

export function createWorkflowRunsActions(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
): WorkflowRunsActions {
  let listToken = 0;
  let activeToken = 0;

  async function refreshList(): Promise<void> {
    const token = ++listToken;
    store.setState((state) => ({
      workflowRuns: { ...state.workflowRuns, listStatus: 'loading', listError: null },
    }));
    try {
      const result = await bus.query('workflow.runs.list', { limit: 200 });
      if (token !== listToken) {
        return;
      }
      store.setState((state) => ({
        workflowRuns: {
          ...state.workflowRuns,
          runs: result.runs,
          listStatus: 'ready',
          listError: null,
        },
      }));
    } catch (error: unknown) {
      if (token !== listToken) {
        return;
      }
      store.setState((state) => ({
        workflowRuns: {
          ...state.workflowRuns,
          listStatus: 'error',
          listError: error instanceof Error ? error.message : String(error),
        },
      }));
    }
  }

  async function refreshActive(): Promise<void> {
    const workflowId = store.getState().workflowRuns.activeWorkflowId;
    if (workflowId === null) {
      return;
    }
    const token = ++activeToken;
    store.setState((state) => ({
      workflowRuns: { ...state.workflowRuns, status: 'loading', error: null },
    }));
    try {
      const result = await bus.query('workflow.runs.get', {
        workflow_id: workflowId,
        include_waits: false,
      });
      if (token !== activeToken || store.getState().workflowRuns.activeWorkflowId !== workflowId) {
        return;
      }
      if (!result.ok || result.run == null) {
        store.setState((state) => ({
          workflowRuns: {
            ...state.workflowRuns,
            activeWorkflowId: workflowId,
            activeRun: null,
            status: 'error',
            error: result.error ?? 'workflow run not found',
          },
        }));
        return;
      }
      store.setState((state) => ({
        workflowRuns: {
          ...state.workflowRuns,
          activeWorkflowId: workflowId,
          activeRun: result.run ?? null,
          status: 'ready',
          error: null,
        },
      }));
    } catch (error: unknown) {
      if (token !== activeToken || store.getState().workflowRuns.activeWorkflowId !== workflowId) {
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
    refreshList,
    async setActive(workflowId): Promise<void> {
      ++activeToken;
      store.setState((state) => ({
        workflowRuns: {
          ...state.workflowRuns,
          activeWorkflowId: workflowId,
          activeRun: null,
          status: workflowId === null ? 'idle' : 'loading',
          error: null,
        },
      }));
      if (workflowId !== null) {
        await refreshActive();
      }
    },
    refreshActive,
    async refresh(): Promise<void> {
      await Promise.all([refreshList(), refreshActive()]);
    },
  };
}
