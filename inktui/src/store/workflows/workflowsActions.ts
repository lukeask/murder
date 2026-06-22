/**
 * Workflows actions — the *only* code that calls the bus for the workflow registry (rule 3).
 *
 * Three RPCs, mirroring the templates pair plus a fire verb:
 *  - `tui.load_workflows {}` → `{ ok, workflows: WorkflowDef[] }` — load the persisted registry.
 *  - `tui.save_workflows { workflows }` → `{ ok, workflows }` — persist it; the reply carries the
 *    NORMALIZED list, so a successful save SYNCS the slice to `result.workflows`.
 *  - `tui.run_workflow { name, args }` → `{ ok, run_ticket_id, stage_ticket_ids, created_ticket_ids }`
 *    — FIRE a saved workflow: the backend materializes the ticket tree + spawns crows. Fire-and-forget
 *    from the UI's view — the materialized tickets/crows arrive via the normal snapshot stream.
 * Declared via a `declare module` augmentation of the shared {@link RpcMethods} registry, so the
 * C1/C2 bus files (`BusClient.ts`/`UdsBusClient.ts`) stay byte-identical — the seam (rule 4). The
 * keys here are distinct from every other slice's keys.
 *
 * ## Optimistic local-first writes
 *
 * `save`/`remove`/`rename` mutate the local `items` immediately (the registry must feel instant) and
 * THEN fire `tui.save_workflows` with the new list. On success the slice is replaced with the server's
 * normalized echo. A save rejection sets `error` + toasts but does NOT roll back the local list — the
 * user's intent stands for the session; a reconnect re-loads from the persisted truth (matching
 * templates/favorites).
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import type { WorkflowDef } from './workflowsSlice.js';

/**
 * The workflow-registry RPC declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files (rule 4 — the seam). Keys distinct from every other slice's.
 * Shapes mirror the bus contract: a {@link WorkflowDef} list round-tripped for load/save, and a fire
 * verb returning the materialized ticket ids.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Load the persisted workflow registry. Empty params; reply carries the saved workflows. */
    'tui.load_workflows': {
      params: Record<string, never>;
      result: { ok: boolean; workflows: readonly WorkflowDef[] };
    };
    /** Persist the registry. Echoes back the NORMALIZED list. */
    'tui.save_workflows': {
      params: { workflows: readonly WorkflowDef[] };
      result: { ok: boolean; workflows: readonly WorkflowDef[] };
    };
    /** Fire a saved workflow by name: materialize its ticket tree + spawn crows. `args` are the
     * stage-instruction substitutions (v0: a single `{input}` key — see fireWorkflow.ts). */
    'tui.run_workflow': {
      params: { name: string; args: Record<string, string> };
      result: {
        ok: boolean;
        run_ticket_id: string;
        stage_ticket_ids: Record<string, string>;
        created_ticket_ids: readonly string[];
      };
    };
  }
}

/** The workflows actions, bound to one {@link BusClient} + store handle. */
export interface WorkflowsActions {
  /**
   * Load the persisted workflows via `tui.load_workflows` (once, at startup). Ref-swaps the slice to
   * `loading`, then `ready` with the loaded list (or `error` on rejection — never thrown past the
   * action, so the startup prime stays fire-and-forget).
   */
  load(): Promise<void>;
  /**
   * Upsert a workflow by name (replace the def if the name exists, else append), then persist via
   * `tui.save_workflows`. On success the slice syncs to the server's normalized echo. Local-first.
   */
  save(defn: WorkflowDef): Promise<void>;
  /** Delete the workflow with `name`, then persist the reduced list. Local-first. */
  remove(name: string): Promise<void>;
  /**
   * Rename `oldName` → `newName`, preserving the def body, then persist. A no-op if `oldName` is
   * absent. Local-first.
   */
  rename(oldName: string, newName: string): Promise<void>;
  /**
   * FIRE a saved workflow via `tui.run_workflow`. Fire-and-forget from the UI's view — the backend
   * materializes the ticket tree + crows, which arrive via the normal snapshot stream. A success
   * toasts the run ticket id; a failure toasts the error (mirroring the optimistic-commit error path).
   */
  run(name: string, args: Record<string, string>): Promise<void>;
}

/** Project a `tui.load_workflows` reply's list defensively (the wire may omit it). */
function toItems(workflows: readonly WorkflowDef[] | undefined): readonly WorkflowDef[] {
  return workflows ?? [];
}

export function createWorkflowsActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): WorkflowsActions {
  /**
   * Ref-swap the local list (optimistic), then persist via `tui.save_workflows`. On success replace
   * the slice with the server's normalized echo; on failure set `error` + toast (NO rollback).
   */
  async function commit(next: readonly WorkflowDef[]): Promise<void> {
    store.setState((state) => ({
      workflows: { ...state.workflows, items: next, status: 'ready', error: null },
    }));
    try {
      const reply = await bus.rpc('tui.save_workflows', { workflows: next });
      store.setState({
        workflows: { items: toItems(reply.workflows), status: 'ready', error: null },
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      // Optimistic fire-and-forget write: the list already changed locally; the slice `error` field
      // is rendered by no view, so the rejection surfaces via the global toast (matching templates).
      // The local list is NOT rolled back — the user's intent stands; a reconnect re-loads truth.
      store.setState((state) => ({ workflows: { ...state.workflows, error: message } }));
      toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
    }
  }

  return {
    async load(): Promise<void> {
      store.setState((state) => ({ workflows: { ...state.workflows, status: 'loading' } }));
      try {
        const reply = await bus.rpc('tui.load_workflows', {});
        store.setState({
          workflows: { items: toItems(reply.workflows), status: 'ready', error: null },
        });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          workflows: { ...state.workflows, status: 'error', error: message },
        }));
      }
    },

    async save(defn: WorkflowDef): Promise<void> {
      const current = store.getState().workflows.items;
      const exists = current.some((w) => w.name === defn.name);
      const next = exists
        ? current.map((w) => (w.name === defn.name ? defn : w))
        : [...current, defn];
      await commit(next);
    },

    async remove(name: string): Promise<void> {
      const next = store.getState().workflows.items.filter((w) => w.name !== name);
      await commit(next);
    },

    async rename(oldName: string, newName: string): Promise<void> {
      const current = store.getState().workflows.items;
      if (!current.some((w) => w.name === oldName)) {
        return; // nothing to rename — no write, no RPC.
      }
      const next = current.map((w) => (w.name === oldName ? { ...w, name: newName } : w));
      await commit(next);
    },

    async run(name: string, args: Record<string, string>): Promise<void> {
      try {
        const reply = await bus.rpc('tui.run_workflow', { name, args });
        // Fire confirmation: the run ticket is the root of the materialized tree; the tickets/crows
        // themselves arrive via the snapshot stream (this action does not poll them).
        toastStore.getState().push(`fired :${name}: → ${reply.run_ticket_id}`);
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        // Mirror the commit() error path: a fire failure surfaces via the global toast.
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      }
    },
  };
}
