/**
 * Spawn actions — the *only* code that spawns a rogue crow (rule 3).
 *
 * Covers the spawn operation triggered from the C13 spawn wizard (`ctrl+s`):
 *  - `crow.spawn_rogue` — spawn a new rogue crow.
 *
 * ## Bus status — command kind, not a standalone RPC (F2)
 *
 * `crow.spawn_rogue` is an **orchestrator command kind**, dispatched through the LIVE
 * `command.submit` choke point ({@link ../commandSubmit.js}), not a direct RPC. The live handler
 * (`Orchestrator.spawn_rogue_command`, `murder/runtime/orchestration/orchestrator.py`) REQUIRES
 * `harness` + `model` and accepts optional `effort` / `name` / `worktree_*`. It does NOT accept a
 * `kickoff_message` — the kickoff is delivered separately as an `agent.message` command to the
 * freshly spawned agent (the submit returns its `agent_id`).
 *
 * The `command.submit`/`command.status` RPC types live in the base `RpcMethods` (`../../bus/
 * BusClient.ts`); no per-slice `declare module` is needed here.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { submitCommand } from '../commandSubmit.js';
import type { AppStore } from '../store.js';

/**
 * The params for spawning a rogue crow — the fields the LIVE `crow.spawn_rogue` command handler
 * requires/accepts. `harness` + `model` are required; `effort` + `name` are optional.
 *
 * NOTE: `kickoff_message` is NOT a field here — the live handler ignores it. The kickoff is
 * delivered as a separate `agent.message` command after the spawn resolves with an `agent_id`
 * (see {@link SpawnActions.spawnRogue}).
 */
export interface SpawnRogueParams {
  /** Harness id (e.g. `'claude'`, `'codex'`). REQUIRED by the live handler. */
  readonly harness: string;
  /** Model id. REQUIRED by the live handler. */
  readonly model: string;
  /** Per-harness effort enum string (e.g. `'low'`, `'medium'`, `'high'`). Optional. */
  readonly effort?: string;
  /** Optional rogue name. */
  readonly name?: string;
  /**
   * Optional existing-worktree path to run the rogue in. Mutually exclusive with `worktreeBranch`.
   * Threaded to the live handler's `worktree_path` param (orchestrator.py:487).
   */
  readonly worktreePath?: string;
  /**
   * Optional new-worktree branch name. When set, the live handler creates a named worktree on that
   * branch and runs the rogue there. Threaded to `worktree_branch` (orchestrator.py:488).
   */
  readonly worktreeBranch?: string;
  /**
   * Optional kickoff instruction. When present, it is delivered AFTER the spawn as a separate
   * `agent.message` command to the spawned agent (the live `crow.spawn_rogue` handler ignores any
   * kickoff field, so it must be sent out-of-band). The locked mechanism is reference-by-path:
   * the message tells the rogue to *read* `.murder/<dir>/<name>.md` rather than inlining the body.
   */
  readonly kickoffMessage?: string | null;
}

/** Reply from spawning a rogue: the orchestrator worker returns the spawned agent's id. */
export interface SpawnRogueResult {
  /** Whether the spawn was accepted. */
  readonly handled: boolean;
  /** The id of the spawned rogue crow, if available. */
  readonly agent_id?: string;
}

/** The actions exposed to the spawn wizard for writing operations. */
export interface SpawnActions {
  /**
   * Spawn a new rogue crow via the `crow.spawn_rogue` command kind (through `command.submit`).
   * Sends the fields the live handler requires (`harness`, `model`, optional `effort`/`name`).
   * When `kickoffMessage` is set, delivers it AFTER the spawn as an `agent.message` command to the
   * returned `agent_id` (the live spawn handler ignores kickoff fields, so it rides out-of-band).
   * Resolves with the result on success; rejects on bus/command error — the caller handles it.
   */
  spawnRogue(params: SpawnRogueParams): Promise<SpawnRogueResult>;
}

/**
 * Build the spawn actions bound to one injected {@link BusClient}, and (optionally) the app store
 * handle so a successful spawn can auto-open the rogue's chat pane (item 9e).
 *
 * When `store` is supplied, `spawnRogue` opens the spawned rogue's chat pane override and pins it as
 * the active pane the moment the spawn resolves with an `agent_id` — so the rogue's history appears
 * on the Stage with no manual step (the roster row itself arrives via a later `state.snapshot`
 * event). When `store` is omitted the spawn still works; only the auto-open side effect is skipped.
 *
 * Rule 3: this is the ONLY caller of the bus for spawn. Components never touch bus.rpc.
 */
export function createSpawnActions(bus: BusClient, store?: StoreApi<AppStore>): SpawnActions {
  return {
    async spawnRogue(params: SpawnRogueParams): Promise<SpawnRogueResult> {
      const payload: Record<string, unknown> = {
        harness: params.harness,
        model: params.model,
      };
      if (params.effort != null) {
        payload['effort'] = params.effort;
      }
      if (params.name != null) {
        payload['name'] = params.name;
      }
      // Worktree threading (snake_case to match the live handler). worktree_branch wins if both are
      // somehow set, mirroring the handler's branch-first precedence (orchestrator.py:512).
      if (params.worktreeBranch != null && params.worktreeBranch !== '') {
        payload['worktree_branch'] = params.worktreeBranch;
      } else if (params.worktreePath != null && params.worktreePath !== '') {
        payload['worktree_path'] = params.worktreePath;
      }
      const result = await submitCommand(bus, 'crow.spawn_rogue', payload);
      const agentId = result['agent_id'] != null ? String(result['agent_id']) : undefined;

      // Deliver the kickoff message out-of-band: the live spawn handler ignores it, so it must go
      // to the freshly spawned agent as a separate `agent.message` command (reference-by-path).
      if (params.kickoffMessage != null && params.kickoffMessage !== '' && agentId !== undefined) {
        await submitCommand(bus, 'agent.message', {
          agent_id: agentId,
          message: params.kickoffMessage,
        });
      }

      // Auto-open the rogue's chat pane on the Stage (item 9e): force its pane override open and pin
      // it active. Guarded on `store` so a store-less construction (e.g. a bare unit test) is inert.
      if (agentId !== undefined && store !== undefined) {
        const conversations = store.getState().actions.conversations;
        conversations.setChatPaneOpen(agentId, true);
        conversations.setActivePaneAgentId(agentId);
      }

      const handled = result['handled'] === true || agentId !== undefined;
      return agentId !== undefined ? { handled, agent_id: agentId } : { handled };
    },
  };
}
