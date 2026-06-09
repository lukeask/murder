/**
 * Harness-models actions — the *only* code that calls the bus for the spawn wizard's per-harness
 * model list (rule 3).
 *
 * ## Pull-only RPC (Workstream A contract)
 *
 * The spawn wizard's model picker is driven by a **pull-only** RPC `state.harness_models_snapshot`
 * built in parallel by Workstream A. The LOCKED response shape is:
 *
 * ```json
 * { "models": { "<harness_kind>": [ {"id": "...", "label": "..."}, ... ] }, "as_of": "<ISO|null>" }
 * ```
 *
 * The wizard fetches the whole map ONCE on open and re-indexes per selected harness (it does NOT
 * replicate Textual's per-harness async discovery worker). When a harness key is missing/empty, the
 * caller falls back to the static last-good map ({@link STATIC_HARNESS_MODELS}).
 *
 * ## INTEGRATION SWAP POINT (Workstream A)
 *
 * `state.harness_models_snapshot` is NOT yet on the live bus — it is modeled here per the contract
 * (same idiom as the B13-modeled `state.notes_snapshot`). {@link fetchHarnessModels} calls the real
 * `bus.rpc('state.harness_models_snapshot', {})`; until Workstream A lands the handler, that call
 * rejects and we fall back to {@link STATIC_HARNESS_MODELS}. When Workstream A lands, nothing here
 * changes — the live handler simply starts answering. The static fallback then only serves as the
 * pre-resolve "last-good" snapshot.
 */

import type { BusClient } from '../../bus/BusClient.js';

/** A single selectable model: an id (sent on the wire) + a human label (shown in the list). */
export interface HarnessModel {
  readonly id: string;
  readonly label: string;
}

/**
 * The `state.harness_models_snapshot` reply — the LOCKED Workstream A shape. `models` maps each
 * harness kind to its model list; `as_of` is the ISO timestamp of the snapshot (or `null`).
 */
export interface HarnessModelsSnapshotReply {
  readonly models: Record<string, readonly HarnessModel[]>;
  readonly as_of: string | null;
}

/**
 * Declares the pull-only models RPC via declaration merging (rule 4 — never edit the frozen C1 bus
 * files). `state.harness_models_snapshot` is the bus-contract name; the params are empty (pull the
 * whole map). NOT yet on the live bus — confirm when Workstream A lands.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Pull the full per-harness model map (Workstream A). Fetched once on wizard open. */
    'state.harness_models_snapshot': {
      params: Record<string, never>;
      result: HarnessModelsSnapshotReply;
    };
  }
}

/**
 * Static last-good model map — the fallback shown instantly on open before (or instead of) the
 * live snapshot. Mirrors the old Textual wizard's `_HARNESS_MODELS` (spawn_wizard.py:21). Harnesses
 * with no static list (`cursor`, `pi`, `antigravity`, `native_coding_crow`) get `[]` → the model
 * step is skipped unless the live snapshot supplies entries.
 */
export const STATIC_HARNESS_MODELS: Record<string, readonly HarnessModel[]> = {
  claude_code: [
    { id: 'sonnet', label: 'Sonnet' },
    { id: 'opus', label: 'Opus' },
    { id: 'haiku', label: 'Haiku' },
  ],
  codex: [
    { id: 'gpt-5.5', label: 'GPT-5.5' },
    { id: 'gpt-5.4', label: 'GPT-5.4' },
    { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
    { id: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' },
    { id: 'gpt-5.2', label: 'GPT-5.2' },
  ],
  cursor: [],
  pi: [],
  antigravity: [],
  native_coding_crow: [],
};

/**
 * The actions exposed to the spawn wizard for the model list (rule 3: the only bus caller).
 */
export interface HarnessModelsActions {
  /**
   * Fetch the full per-harness model map. Resolves with the live snapshot's `models` map on
   * success, or {@link STATIC_HARNESS_MODELS} on any rejection (RPC not live / transport error) —
   * never throws past the action. The wizard re-indexes the returned map per selected harness.
   */
  fetch(): Promise<Record<string, readonly HarnessModel[]>>;
}

/**
 * Build the harness-models actions bound to one injected {@link BusClient}. No store handle: the
 * model map is wizard-local closure state (a one-shot pull on open), not a global slice.
 */
export function createHarnessModelsActions(bus: BusClient): HarnessModelsActions {
  return {
    async fetch(): Promise<Record<string, readonly HarnessModel[]>> {
      try {
        const reply = await bus.rpc('state.harness_models_snapshot', {});
        // Merge over the static map so a harness the snapshot omits still shows its last-good list.
        return { ...STATIC_HARNESS_MODELS, ...reply.models };
      } catch {
        // RPC not live (Workstream A not landed) or transport error — fall back to last-good.
        return STATIC_HARNESS_MODELS;
      }
    },
  };
}

/**
 * Pure: the model list for one harness, given a fetched (or static) map. Returns `[]` when the
 * harness key is missing or empty — the caller skips the model step in that case. This is the
 * single dependent-field derivation for models (kept pure so the step machine is correct by
 * construction and unit-testable without rendering).
 */
export function modelsFor(
  harness: string,
  map: Record<string, readonly HarnessModel[]>,
): readonly HarnessModel[] {
  return map[harness] ?? [];
}
