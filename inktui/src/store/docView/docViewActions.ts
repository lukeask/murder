/**
 * Doc-view actions — the *only* code that calls the bus to fetch a document body (rule 3).
 *
 * Three LIVE per-kind read RPCs (one per {@link DocKind}), registered in
 * `murder/app/service/host.py`:
 *  - `state.plan_display   { name }` → {@link DisplaySnapshot}
 *  - `state.note_display   { name }` → {@link DisplaySnapshot}
 *  - `state.report_display { name }` → {@link DisplaySnapshot}
 *
 * The reply is a `DisplaySnapshot` ({@link DisplaySnapshot} — `{ name, markdown }`), NOT a bare
 * `{ body }`. `open` selects the method by kind and reads `reply.markdown`.
 *
 * Declared via `declare module` augmentation of the shared {@link RpcMethods} registry, so the
 * C1/C2 bus files stay byte-identical (rule 4 — the seam). Each key is distinct from every other
 * slice's keys. The pure RPC-consumer rule means the view never reads `.murder/<dir>/<name>.md`
 * itself; the service owns the filesystem and returns the markdown.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { DocKind } from './docViewSlice.js';

/**
 * The doc-display RPCs, augmenting the shared {@link RpcMethods} registry without editing the frozen
 * C1/C2 bus files. All three are LIVE on the bus (registered in `murder/app/service/host.py`).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch a plan's display snapshot by name. The service reads the file; the view never touches
     * disk (pure RPC consumer). */
    'state.plan_display': { params: { name: string }; result: DisplaySnapshot };
    /** Fetch a note's display snapshot by name. */
    'state.note_display': { params: { name: string }; result: DisplaySnapshot };
    /** Fetch a report's display snapshot by name. */
    'state.report_display': { params: { name: string }; result: DisplaySnapshot };
  }
}

/**
 * The `state.{plan,note,report}_display` reply — the live `*DisplaySnapshot` DTO from
 * `murder/app/service/client_api.py`. Carries the document name + its full markdown (NOT a bare
 * `{ body }`). The wire may carry more metadata in future; only the consumed fields are typed.
 */
export interface DisplaySnapshot {
  /** The document name (echoed from the request). */
  readonly name: string;
  /** The full markdown content of the requested document. */
  readonly markdown: string;
}

/** Map a {@link DocKind} to its live per-kind display RPC method name. */
const DISPLAY_METHOD = {
  plan: 'state.plan_display',
  note: 'state.note_display',
  report: 'state.report_display',
} as const satisfies Record<DocKind, keyof import('../../bus/BusClient.js').RpcMethods>;

/** The doc-view actions, bound to one {@link BusClient} + store handle. */
export interface DocViewActions {
  /**
   * Open a document: ref-swap the slice to show `{ kind, name }` in `loading`, fetch its body via
   * the per-kind `state.{plan,note,report}_display` RPC, then ref-swap to `ready` with the body
   * (from the `DisplaySnapshot.markdown` field) (or `error` on rejection — never thrown past
   * the action). Calling `open` with a doc already open replaces it (re-fetches). The doc-view Stage
   * pane bridge focuses it separately; this action only loads data.
   */
  open(kind: DocKind, name: string): Promise<void>;
  /**
   * Close the doc view — ref-swap the slice back to its initial (closed) state. Called when the
   * doc-view mode is dismissed (enter on the shown doc minimises it). No bus call.
   */
  close(): void;
}

export function createDocViewActions(bus: BusClient, store: StoreApi<AppStore>): DocViewActions {
  return {
    async open(kind: DocKind, name: string): Promise<void> {
      store.setState({
        docView: { open: { kind, name }, body: null, status: 'loading', error: null },
      });
      try {
        const reply = await bus.rpc(DISPLAY_METHOD[kind], { name });
        store.setState((state) => {
          // Guard against a stale reply: only apply if this doc is still the open one.
          const cur = state.docView.open;
          if (cur === null || cur.kind !== kind || cur.name !== name) {
            return state;
          }
          return {
            docView: { ...state.docView, body: reply.markdown, status: 'ready', error: null },
          };
        });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => {
          const cur = state.docView.open;
          if (cur === null || cur.kind !== kind || cur.name !== name) {
            return state;
          }
          return { docView: { ...state.docView, status: 'error', error: message } };
        });
      }
    },

    close(): void {
      store.setState({
        docView: { open: null, body: null, status: 'idle', error: null },
      });
    },
  };
}
