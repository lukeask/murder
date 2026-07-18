/**
 * Templates actions — the *only* code that calls the bus for the template registry (rule 3).
 *
 * Two RPCs, mirroring the bus contract's prefs pair:
 *  - `tui.load_templates {}` → `{ ok, templates: [{name,body},…] }` — load the persisted registry.
 *  - `tui.save_templates { templates }` → `{ ok, templates }` — persist it; the reply carries the
 *    NORMALIZED list (names validated, de-duped last-wins, sorted), so a successful save SYNCS the
 *    slice to `result.templates`.
 * Declared via a `declare module` augmentation of the shared {@link RpcMethods} registry, so the
 * C1/C2 bus files (`BusClient.ts`/`UdsBusClient.ts`) stay byte-identical — the seam (rule 4). The
 * keys here (`tui.load_templates`/`tui.save_templates`) are distinct from every other slice's keys.
 *
 * ## Optimistic local-first writes
 *
 * `save`/`remove`/`rename` mutate the local `items` immediately (the registry must feel instant) and
 * THEN fire `tui.save_templates` with the new list. On success the slice is replaced with the
 * server's normalized echo. A save rejection sets `error` + toasts but does NOT roll back the local
 * list — the user's intent stands for the session; a reconnect re-loads from the persisted truth
 * (matching favorites).
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import type { TemplateRecord } from './templatesSlice.js';

/**
 * The template-registry RPC declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files (rule 4 — the seam). Keys distinct from every other slice's.
 * Shapes mirror the bus contract: a `{ name, body }` list, round-tripped in both directions, the
 * save reply carrying the backend-normalized list.
 */
declare module '../../bus/BusClient.js' {
  interface QueryMethods {
    /** Load the persisted template registry. Empty params; reply carries the saved templates. */
    'templates.get': {
      params: Record<string, never>;
      result: { ok: boolean; templates: readonly TemplateRecord[] };
    };
  }
  interface CommandMethods {
    /** Persist the registry. Echoes back the NORMALIZED (validated/de-duped/sorted) list. */
    'templates.set': {
      params: { templates: readonly TemplateRecord[] };
      result: { ok: boolean; templates: readonly TemplateRecord[] };
    };
  }
}

/** The templates actions, bound to one {@link BusClient} + store handle. */
export interface TemplatesActions {
  /**
   * Load the persisted templates via `tui.load_templates` (once, at startup). Ref-swaps the slice to
   * `loading`, then `ready` with the loaded list (or `error` on rejection — never thrown past the
   * action, so the startup prime stays fire-and-forget).
   */
  load(): Promise<void>;
  /**
   * Upsert a template by name (replace body if the name exists, else append), then persist via
   * `tui.save_templates`. On success the slice syncs to the server's normalized echo. Local-first.
   */
  save(name: string, body: string): Promise<void>;
  /** Delete the template with `name`, then persist the reduced list. Local-first. */
  remove(name: string): Promise<void>;
  /**
   * Rename `oldName` → `newName`, preserving the body, then persist. A no-op if `oldName` is absent.
   * Local-first.
   */
  rename(oldName: string, newName: string): Promise<void>;
}

/** Project a `tui.load_templates` reply's list defensively (the wire may omit it). */
function toItems(templates: readonly TemplateRecord[] | undefined): readonly TemplateRecord[] {
  return templates ?? [];
}

export function createTemplatesActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): TemplatesActions {
  /**
   * Ref-swap the local list (optimistic), then persist via `tui.save_templates`. On success replace
   * the slice with the server's normalized echo; on failure set `error` + toast (NO rollback).
   */
  async function commit(next: readonly TemplateRecord[]): Promise<void> {
    store.setState((state) => ({
      templates: { ...state.templates, items: next, status: 'ready', error: null },
    }));
    try {
      const reply = await bus.command('templates.set', { templates: next });
      store.setState({
        templates: { items: toItems(reply.templates), status: 'ready', error: null },
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      // Optimistic fire-and-forget write: the list already changed locally; the slice `error` field
      // is rendered by no view, so the rejection surfaces via the global toast (matching favorites).
      // The local list is NOT rolled back — the user's intent stands; a reconnect re-loads truth.
      store.setState((state) => ({ templates: { ...state.templates, error: message } }));
      toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
    }
  }

  return {
    async load(): Promise<void> {
      store.setState((state) => ({ templates: { ...state.templates, status: 'loading' } }));
      try {
        const reply = await bus.query('templates.get', {});
        store.setState({
          templates: { items: toItems(reply.templates), status: 'ready', error: null },
        });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          templates: { ...state.templates, status: 'error', error: message },
        }));
      }
    },

    async save(name: string, body: string): Promise<void> {
      const current = store.getState().templates.items;
      const exists = current.some((t) => t.name === name);
      const next = exists
        ? current.map((t) => (t.name === name ? { name, body } : t))
        : [...current, { name, body }];
      await commit(next);
    },

    async remove(name: string): Promise<void> {
      const next = store.getState().templates.items.filter((t) => t.name !== name);
      await commit(next);
    },

    async rename(oldName: string, newName: string): Promise<void> {
      const current = store.getState().templates.items;
      if (!current.some((t) => t.name === oldName)) {
        return; // nothing to rename — no write, no RPC.
      }
      const next = current.map((t) => (t.name === oldName ? { name: newName, body: t.body } : t));
      await commit(next);
    },
  };
}
