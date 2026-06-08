/**
 * Doc-view actions — the *only* code that calls the bus to fetch a document body (rule 3).
 *
 * One read RPC, modeled per bus convention (`domain.verb`):
 *  - `doc.get { kind, name }` → `{ body }` — fetch a plan/note/report's markdown by kind + name.
 *
 * Declared via `declare module` augmentation of the shared {@link RpcMethods} registry, so the
 * C1/C2 bus files stay byte-identical (rule 4 — the seam). The key (`doc.get`) is distinct from
 * every other slice's keys.
 *
 * ## Bus status: MODELED, NOT LIVE
 *
 * `doc.get` is not on the live bus yet — it lands with service B13. The pure RPC-consumer rule means
 * the view never reads `.murder/<dir>/<name>.md` itself; the service owns the filesystem and returns
 * the body. Until B13, `open` resolves against the `FakeBusClient` stub; a live `UdsBusClient` would
 * reject and the action routes that into the slice's `error` field (never thrown past the action).
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { DocKind } from './docViewSlice.js';

/**
 * C11's doc-fetch RPC, augmenting the shared {@link RpcMethods} registry without editing the frozen
 * C1/C2 bus files. **Bus status: MODELED, NOT LIVE** (lands with service B13).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch a document's markdown body by kind + name. The service reads the file; the view never
     * touches disk (pure RPC consumer). */
    'doc.get': { params: { kind: DocKind; name: string }; result: DocGetReply };
  }
}

/** The `doc.get` reply — the document's markdown body. */
export interface DocGetReply {
  /** The full markdown content of the requested document. */
  body: string;
}

/** The doc-view actions, bound to one {@link BusClient} + store handle. */
export interface DocViewActions {
  /**
   * Open a document: ref-swap the slice to show `{ kind, name }` in `loading`, fetch its body via
   * `doc.get`, then ref-swap to `ready` with the body (or `error` on rejection — never thrown past
   * the action). Calling `open` with a doc already open replaces it (re-fetches). The doc-view mode
   * (`DocViewMode`) enters itself separately; this action only loads the data.
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
        const reply = await bus.rpc('doc.get', { kind, name });
        store.setState((state) => {
          // Guard against a stale reply: only apply if this doc is still the open one.
          const cur = state.docView.open;
          if (cur === null || cur.kind !== kind || cur.name !== name) {
            return state;
          }
          return { docView: { ...state.docView, body: reply.body, status: 'ready', error: null } };
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
