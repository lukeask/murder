/**
 * Generic list-slice mechanics — the factory the four domain quads (roster, notes, reports,
 * tickets) share instead of copy-pasting ~80% byte-identical slice/action code.
 *
 * Every domain slice in this app is the same shape: a `{ rows, status, error }` triple, fed by a
 * single "read the whole snapshot" RPC, ref-swapping *only* its own key on each `state.snapshot`
 * invalidation (the granularity contract — see `./store.ts`). The ONLY things that differ per
 * domain are: the row type, the slice key, the RPC method name, and the DTO→rows projection. This
 * module captures everything else exactly once.
 *
 * What lives here (rule 4: framework-/transport-agnostic — no Ink, no React, no socket):
 *  - {@link ListState} / {@link initialListState}: the shared state shape + its idle boot value.
 *  - {@link createListSlice}: the trivial Zustand `StateCreator` that seeds one slice key.
 *  - {@link createRefreshAction}: the shared `refresh()` mechanics — loading → rpc → project →
 *    ready, with rejections routed into the slice's `error` field (never thrown past the action).
 *
 * The projection (`project`) is the per-domain injection point. Roster's `.sessions.map(...)` and
 * tickets' active+recent_done+archived 3-bucket flatten both live in their own `project` closures —
 * the generic never special-cases a domain; it just calls the injected fn (the rule-of-three fix
 * keeps the divergence as data, not as a branch in here).
 *
 * To add slice X: copy the three roster files (`rosterSlice.ts`/`rosterActions.ts` +
 * `rosterSelectors.ts`). The slice/action files are now ~thin shells over this factory — you only
 * supply X's row type, RPC method + reply type, and the `project` fn; all the loading/error/
 * ref-swap mechanics come from here unchanged. See `rosterSlice.ts`/`rosterActions.ts` for the
 * canonical example and `store.ts` for the (still ≈5 additive edits) composition wiring.
 */

import type { StateCreator, StoreApi } from 'zustand';
import type { BusClient, RpcMethods } from '../bus/BusClient.js';
import type { AppStore } from './store.js';

/**
 * The state shape every domain slice shares. `rows` is the presentation-free domain data; `status`
 * makes the load lifecycle explicit so a component can distinguish "not fetched yet" from "fetched,
 * empty" without a sentinel; `error` is set when the last refresh rejected and cleared on the next
 * successful load. Every field is readonly — the slice is ref-swapped wholesale on change (the
 * invalidation-granularity contract), never mutated in place. Selectors read `XState['status']`
 * off this, so the `status` union is part of the public contract.
 */
export interface ListState<Row> {
  readonly rows: readonly Row[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/** The initial, pre-fetch value for any list slice. A fresh store has not talked to the bus → `idle`. */
export function initialListState<Row>(): ListState<Row> {
  return { rows: [], status: 'idle', error: null };
}

/**
 * The trivial Zustand `StateCreator` that seeds one slice key with its idle initial state. The
 * slice holds state, not actions: mutation is done by the action layer (see {@link createRefreshAction})
 * calling `set` through the store handle, keeping the bus dependency out of this framework-agnostic
 * file (rule 4). `Key` is the slice's top-level key in {@link AppStore}; `Row` is its row type.
 */
export function createListSlice<Key extends keyof AppStore & string, Row>(
  key: Key,
  initial: ListState<Row>,
): StateCreator<AppStore, [], [], Record<Key, ListState<Row>>> {
  return () => ({ [key]: initial }) as Record<Key, ListState<Row>>;
}

/**
 * Build the shared `refresh()` action for one list slice. The single bus caller for that domain
 * (rule 3): it ref-swaps the slice to `loading`, issues one RPC, projects the reply into rows via
 * the injected `project` fn, and ref-swaps the slice to `ready` (or `error` on rejection — never
 * thrown past the action, so the invalidation loop in `store.ts` stays fire-and-forget).
 *
 * @param key     the slice's top-level key in {@link AppStore} (also the `setState` target).
 * @param method  the read RPC method name (a key of the {@link RpcMethods} registry).
 * @param project the per-domain DTO→rows projection. This is the divergence injection point:
 *                roster maps `.sessions`, tickets flattens its three buckets, here it's just called.
 *
 * The dynamic-key writes below need one localized cast: TypeScript can't prove
 * `{ [key]: ListState<Row> }` is assignable to `Partial<AppStore>` for an arbitrary generic `key`,
 * even though every `AppStore[Key]` is a `ListState<…>` by construction. The cast is contained to
 * this helper and commented — it is the price of not branching per-domain.
 */
export function createRefreshAction<
  Key extends keyof AppStore & string,
  Method extends keyof RpcMethods,
  Row,
>(
  bus: BusClient,
  store: StoreApi<AppStore>,
  config: {
    readonly key: Key;
    readonly method: Method;
    readonly project: (reply: RpcMethods[Method]['result']) => readonly Row[];
  },
): { refresh(): Promise<void> } {
  const { key, method, project } = config;
  // Per-slice request token: a burst of `state.snapshot` invalidations (or a reconnect re-prime)
  // can fire `refresh()` repeatedly with no ordering guarantee on the RPCs. Without this guard an
  // OLDER reply that resolves last would overwrite a newer one's rows as `ready` (stale clobber).
  // Each call bumps `seq`; a reply only applies if it is still the latest when the RPC settles.
  // A shared drain loop coalesces BOTH synchronous bursts AND async storms (e.g. a WS subscription
  // replay that delivers one snapshot per message): every call bumps `seq`, but only one drain runs
  // at a time and retries until the in-flight RPC matches the final `seq`.
  let seq = 0;
  let drainPromise: Promise<void> | null = null;

  async function drain(): Promise<void> {
    if (drainPromise !== null) {
      return drainPromise;
    }
    drainPromise = (async () => {
      try {
        for (;;) {
          // Macrotask deferral: collapses sync bursts AND back-to-back WS `pub` frames that each
          // schedule their own turn — a subscription replay storm becomes one RPC per slice.
          await new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          });
          const token = seq;
          try {
            const reply = await bus.rpc(method, {} as RpcMethods[Method]['params']);
            if (token !== seq) {
              continue;
            }
            const rows = project(reply);
            const next: ListState<Row> = { rows, status: 'ready', error: null };
            store.setState({ [key]: next } as unknown as Partial<AppStore>);
            return;
          } catch (error: unknown) {
            if (token !== seq) {
              continue;
            }
            const message = error instanceof Error ? error.message : String(error);
            store.setState((state) => {
              const current = state[key] as ListState<Row>;
              return {
                [key]: { ...current, status: 'error', error: message },
              } as unknown as Partial<AppStore>;
            });
            return;
          }
        }
      } finally {
        drainPromise = null;
      }
    })();
    return drainPromise;
  }

  return {
    async refresh(): Promise<void> {
      seq++;
      // Mark loading by ref-swapping ONLY this slice — sibling slices keep their identity. When rows
      // already exist, keep `ready` so the UI does not flash a loading overlay over live data.
      store.setState((state) => {
        const current = state[key] as ListState<Row>;
        const status =
          current.status === 'idle' || current.rows.length === 0 ? 'loading' : current.status;
        // Localized cast: the generic key defeats `Partial<AppStore>` inference (see fn docstring).
        return { [key]: { ...current, status } } as unknown as Partial<AppStore>;
      });
      await drain();
    },
  };
}
