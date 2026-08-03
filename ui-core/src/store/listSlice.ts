/**
 * Generic list-slice mechanics — the factory the domain quads (roster, notes, reports,
 * tickets, …) share instead of copy-pasting ~80% byte-identical slice/action code.
 *
 * Every domain slice in this app is the same shape: a `{ rows, status, error }` triple, fed by a
 * single typed projection query, ref-swapping *only* its own key after a projection invalidation
 * (the granularity contract — see `./store.ts`). Domains differ in: the row type, the slice key(s),
 * the RPC method name, and the DTO→state projection. This module captures everything else once.
 *
 * What lives here (rule 4: framework-/transport-agnostic — no Ink, no React, no socket):
 *  - {@link ListState} / {@link initialListState}: the shared state shape + its idle boot value.
 *  - {@link createListSlice}: the trivial Zustand `StateCreator` that seeds one slice key.
 *  - {@link createRefreshAction}: the shared `refresh()` mechanics — loading → rpc → project →
 *    ready, with rejections routed into the slice `error` field (never thrown past the action).
 *    Supports single-slice and multi-slice sources via one `seq`/drain implementation: `project`
 *    returns a multi-slice patch and the caller declares which keys participate in loading/error.
 *
 * The projection (`project`) is the per-domain injection point. Roster's snapshot projection and
 * schedule's tickets+usage multi-slice patch both live in their own modules — the generic never
 * special-cases a domain; it just calls the injected fn.
 */

import type { StateCreator, StoreApi } from 'zustand';
import type { ApplicationClient, QueryMethod, QueryParams, QueryResult } from '../application/ApplicationClient.js';
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

/** Slice keys that use {@link ListState} and can participate in the shared refresh lifecycle. */
export type ListSliceKey = {
  [K in keyof AppStore]: AppStore[K] extends ListState<infer _Row> ? K : never;
}[keyof AppStore];

export interface RefreshOptions {
  /**
   * Which of the configured keys participate in this call's loading **and** error lifecycle.
   * Defaults to all configured keys. Use a subset when a write workflow (e.g. usage sample) must
   * not disturb sibling slices — siblings stay off both the loading flash and a failed-RPC error.
   * (Success still applies the full projection patch so siblings get fresh rows without a lifecycle
   * transition.)
   */
  readonly loadingKeys?: readonly ListSliceKey[];
}

/** Build a single-slice ready patch from projected rows — the common single-source case. */
export function listReadyPatch<Key extends ListSliceKey, Row>(
  key: Key,
  rows: readonly Row[],
): Pick<AppStore, Key> {
  return {
    [key]: { rows, status: 'ready', error: null },
  } as unknown as Pick<AppStore, Key>;
}

/**
 * Build the shared `refresh()` action for one or more list slices fed by the same query.
 * The single bus caller for that source (rule 3): it ref-swaps participating slices to `loading`,
 * issues one RPC, projects the reply into a multi-slice patch via the injected `project` fn, and
 * applies the patch in one `setState` (or routes the error into the same participating keys — never
 * thrown past the action, so the invalidation loop in `store.ts` stays fire-and-forget).
 *
 * Invariant: for one drain cycle, the keys that enter `loading` are exactly the keys that receive
 * `error` on rejection. Coalesced calls union their lifecycle keys for that cycle.
 *
 * @param keys    default slice keys for loading and error when a call does not pass `loadingKeys`.
 * @param method  the read RPC method name.
 * @param project the per-domain DTO→patch projection. May update one or many slices.
 *
 * The dynamic-key writes below need one localized cast: TypeScript can't prove
 * `{ [key]: ListState<Row> }` is assignable to `Partial<AppStore>` for an arbitrary generic `key`,
 * even though every list slice is a `ListState<…>` by construction. The cast is contained to
 * this helper and commented — it is the price of not branching per-domain.
 */
export function createRefreshAction<Method extends QueryMethod>(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
  config: {
    readonly keys: readonly ListSliceKey[];
    readonly method: Method;
    readonly project: (reply: QueryResult<Method>) => Partial<AppStore>;
  },
): { refresh(options?: RefreshOptions): Promise<void> } {
  const { keys, method, project } = config;
  // Per-source request token: a burst of projection invalidations (or a reconnect re-prime)
  // can fire `refresh()` repeatedly with no ordering guarantee on the RPCs. Without this guard an
  // OLDER reply that resolves last would overwrite a newer one's rows as `ready` (stale clobber).
  // Each call bumps `seq`; a reply only applies if it is still the latest when the RPC settles.
  // A shared drain loop coalesces BOTH synchronous bursts AND async storms (e.g. a WS subscription
  // replay that delivers one snapshot per message): every call bumps `seq`, but only one drain runs
  // at a time and retries until the in-flight RPC matches the final `seq`.
  let seq = 0;
  let drainPromise: Promise<void> | null = null;
  // Union of lifecycle keys across coalesced refresh() calls for the in-flight drain. Cleared only
  // when a reply is applied (success or matching-token failure) so a superseded attempt cannot drop
  // keys a newer call already registered.
  let lifecycleKeys = new Set<ListSliceKey>();

  async function drain(): Promise<void> {
    if (drainPromise !== null) {
      return drainPromise;
    }
    drainPromise = (async () => {
      try {
        for (;;) {
          // Macrotask deferral: collapses sync bursts AND back-to-back WS `pub` frames that each
          // schedule their own turn — a subscription replay storm becomes one RPC per source.
          await new Promise<void>((resolve) => {
            setTimeout(resolve, 0);
          });
          const token = seq;
          try {
            const reply = (await bus.query(
              method,
              {} as QueryParams<Method>,
            )) as QueryResult<Method>;
            if (token !== seq) {
              continue;
            }
            const patch = project(reply);
            lifecycleKeys = new Set();
            store.setState(patch);
            return;
          } catch (error: unknown) {
            if (token !== seq) {
              continue;
            }
            const message = error instanceof Error ? error.message : String(error);
            const errorKeys = [...lifecycleKeys];
            lifecycleKeys = new Set();
            store.setState((state) => {
              const next: Partial<AppStore> = {};
              for (const key of errorKeys) {
                const current = state[key] as ListState<unknown>;
                (next as Record<string, ListState<unknown>>)[key] = {
                  ...current,
                  status: 'error',
                  error: message,
                };
              }
              return next;
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
    async refresh(options?: RefreshOptions): Promise<void> {
      seq++;
      const markKeys = options?.loadingKeys ?? keys;
      for (const key of markKeys) {
        lifecycleKeys.add(key);
      }
      // Mark loading by ref-swapping ONLY the participating slices — siblings keep their identity.
      // When rows already exist, keep `ready` so the UI does not flash a loading overlay over live data.
      store.setState((state) => {
        const next: Partial<AppStore> = {};
        for (const key of markKeys) {
          const current = state[key] as ListState<unknown>;
          const status =
            current.status === 'idle' || current.rows.length === 0 ? 'loading' : current.status;
          (next as Record<string, ListState<unknown>>)[key] = { ...current, status };
        }
        return next;
      });
      await drain();
    },
  };
}
