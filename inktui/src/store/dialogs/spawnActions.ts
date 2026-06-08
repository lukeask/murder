/**
 * Spawn actions — the *only* code that calls the bus for spawning a rogue crow (rule 3).
 *
 * Covers the spawn operation triggered from the C13 spawn wizard (`ctrl+s`):
 *  - `crow.spawn_rogue` — spawn a new rogue crow with an effort level and an optional
 *    spawn-context kickoff message (reference-by-path, not inline body).
 *
 * ## Bus status
 *
 * `crow.spawn_rogue` is listed in the bus contract as **already on the bus** (service B10 carries
 * `effort` end-to-end). The TS signature is declared here via `declare module` augmentation (the
 * C1/C2 bus files stay byte-identical — rule 4 / the seam). The shape here may need confirming
 * against the live service; flag any wire-shape divergence at B13 review time.
 *
 * The RpcMethods augmentation keeps the C1/C2 bus files byte-identical (rule 4 — the seam).
 */

import type { BusClient } from '../../bus/BusClient.js';

/**
 * C13's RPC method declaration, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files. The key is distinct from every other slice's keys.
 *
 * **Bus status:** `crow.spawn_rogue` is on the bus per the bus contract (service B10). The params
 * shape here models `{ effort, kickoff_message? }` per the spec; confirm wire shape when verifying
 * against the live service.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /**
     * Spawn a new rogue crow. `effort` is a per-harness effort enum string (passed through
     * end-to-end by service B10). `kickoff_message` is an optional initial instruction — when a
     * spawn-context doc was selected, the wizard sets this to "Please read .murder/<dir>/<name>.md"
     * (reference-by-path, not inlined body — the locked mechanism from the spec's keybinds section).
     *
     * Bus status: already on the bus (service B10 carries `effort`). Confirm the exact param names
     * and result shape against the running service.
     */
    'crow.spawn_rogue': {
      params: SpawnRogueParams;
      result: SpawnRogueResult;
    };
  }
}

/**
 * The params for `crow.spawn_rogue`.
 *
 * Note: extends `Record<string, unknown>` (via `RpcPayload` compatibility) so the interface
 * satisfies the `RpcMethods` params constraint. Both fields are optional at the index-signature
 * level; the required `effort` is enforced at the call site.
 */
export interface SpawnRogueParams extends Record<string, unknown> {
  /** Per-harness effort enum string (e.g. `'low'`, `'medium'`, `'high'`). */
  effort: string;
  /**
   * Optional kickoff instruction injected by the spawn wizard when a context doc is selected.
   * The locked mechanism is reference-by-path: the message tells the rogue to *read*
   * `.murder/<dir>/<name>.md` rather than inlining the body. This primes the rogue's engagement
   * with the doc (same rationale as ticket crows reading their own ticket).
   *
   * Absent when no context doc was selected (the user pressed `n` or no focused doc was available).
   */
  kickoff_message?: string;
}

/** Reply from `crow.spawn_rogue`. Shape modeled from the bus contract; confirm when B10/service verified. */
export interface SpawnRogueResult {
  /** Whether the spawn was accepted. */
  readonly handled: boolean;
  /** The id of the spawned rogue crow, if available. */
  readonly agent_id?: string;
}

/** The actions exposed to the spawn wizard for writing operations. */
export interface SpawnActions {
  /**
   * Spawn a new rogue crow via `crow.spawn_rogue`.
   * Resolves with the result on success; rejects on bus error.
   * The caller (wizard `onIntent`) handles the rejection.
   */
  spawnRogue(params: SpawnRogueParams): Promise<SpawnRogueResult>;
}

/**
 * Build the spawn actions bound to one injected {@link BusClient}. No store ref needed —
 * spawn is a fire-and-resolve operation, not a slice invalidation (the rogue's appearance
 * in the crows panel will come via a `state.snapshot` event from the service).
 *
 * Rule 3: this is the ONLY caller of the bus for spawn. Components never touch bus.rpc.
 */
export function createSpawnActions(bus: BusClient): SpawnActions {
  return {
    async spawnRogue(params: SpawnRogueParams): Promise<SpawnRogueResult> {
      return bus.rpc('crow.spawn_rogue', params);
    },
  };
}
