/**
 * Spawn actions — the *only* code that spawns rogues and planners (rule 3).
 *
 * Covers spawn operations triggered from the C13 spawn wizard (`ctrl+s`) and the plans panel (`p`):
 *  - `crow.spawn_rogue` — spawn a new rogue crow.
 *  - `planner.spawn` — spawn (or return) a per-plan planning agent.
 *
 * ## Application command status
 *
 * Both kinds are **orchestrator command kinds**, dispatched through the LIVE `command.submit`
 * choke point ({@link ../commandSubmit.js}), not direct RPCs. `crow.spawn_rogue` may be followed by
 * a separate `agent.message` kickoff; planners do not get a UI kickoff because the backend brief is
 * the single prompt source.
 *
 * The `command.submit`/`command.status` RPC types live in the base `RpcMethods` (`../../bus/
 * ApplicationClient.ts`); no per-slice `declare module` is needed here.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { submitCommand } from '../commandSubmit.js';
import type { SettingsState } from '../settings/settingsSlice.js';
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

/**
 * The params for spawning a planning agent — the fields the LIVE `planner.spawn` command handler
 * requires/accepts. `planName` + `harness` are required; `model` + `effort` are optional.
 */
export interface SpawnPlannerParams {
  /** The plan stem (no `.md`). REQUIRED by the live handler. */
  readonly planName: string;
  /** Harness id from Settings → Planning Agent Harness. REQUIRED by the live handler. */
  readonly harness: string;
  /** Model id. Optional; empty string lets the role config / adapter pick its default. */
  readonly model?: string;
  /** Per-harness effort enum string. Optional. */
  readonly effort?: string;
}

/** Reply from spawning a rogue: the orchestrator worker returns the spawned agent's id. */
export interface SpawnRogueResult {
  /** Whether the spawn was accepted. */
  readonly handled: boolean;
  /** The id of the spawned rogue crow, if available. */
  readonly agent_id?: string;
}

/** Reply from spawning a planner: the orchestrator worker returns the planner's agent id. */
export interface SpawnPlannerResult {
  readonly handled: boolean;
  readonly agent_id?: string;
}

/** The actions exposed to spawn entry points for writing operations. */
export interface SpawnActions {
  /**
   * Spawn a new rogue crow via the `crow.spawn_rogue` command kind (through `command.submit`).
   * Sends the fields the live handler requires (`harness`, `model`, optional `effort`/`name`).
   * When `kickoffMessage` is set, delivers it AFTER the spawn as an `agent.message` command to the
   * returned `agent_id` (the live spawn handler ignores kickoff fields, so it rides out-of-band).
   * Resolves with the result on success; rejects on bus/command error — the caller handles it.
   */
  spawnRogue(params: SpawnRogueParams): Promise<SpawnRogueResult>;
  /**
   * Spawn (or return) a per-plan planning agent via `planner.spawn`.
   */
  spawnPlanner(params: SpawnPlannerParams): Promise<SpawnPlannerResult>;
}

/** The orchestrator agent id for a per-plan planning agent. */
export function plannerAgentId(planName: string): string {
  return `planner-${planName}`;
}

/**
 * The spawn params for a planning agent over one plan — the single home for the planner defaults
 * (the `p` bind in the Plans panel and the staged plan doc both spawn through this, so the two entry
 * points can never drift). Pure; exported for unit tests.
 *
 * Planner startup instructions live in the backend brief assembler; the TUI only chooses the
 * harness/model/effort and sends the spawn command.
 *
 * Harness comes from Settings → Planning Agent Harness (`plannerHarness` override, else effective).
 * Model/effort are planning-tier defaults per harness (deep-thinking where the harness supports it).
 */
export function plannerSpawnParams(
  planName: string,
  settings: Pick<SettingsState, 'plannerHarness' | 'effectivePlannerHarness'>,
): SpawnPlannerParams {
  const harness = settings.plannerHarness ?? settings.effectivePlannerHarness;
  const params: SpawnPlannerParams = {
    planName,
    harness,
    model: harness === 'claude_code' ? 'opus' : '',
  };
  if (harness === 'claude_code' || harness === 'codex') {
    return { ...params, effort: 'high' };
  }
  return params;
}

function openSpawnedAgentPane(store: StoreApi<AppStore>, agentId: string): void {
  const conversations = store.getState().actions.conversations;
  conversations.setTranscriptPaneOpen(agentId, true);
  conversations.setActivePaneAgentId(agentId);
  void store.getState().actions.roster.refresh();
}

/**
 * Build the spawn actions bound to one injected {@link ApplicationClient}, and (optionally) the app store
 * handle so a successful spawn can auto-open the agent's transcript pane (item 9e).
 *
 * When `store` is supplied, a successful spawn opens the agent's transcript pane override and pins it
 * as the active pane the moment the spawn resolves with an `agent_id`. When `store` is omitted the
 * spawn still works; only the auto-open side effect is skipped.
 *
 * Rule 3: this is the ONLY caller of the bus for spawn. Components never touch bus.rpc.
 */
export function createSpawnActions(bus: ApplicationClient, store?: StoreApi<AppStore>): SpawnActions {
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

      if (agentId !== undefined && store !== undefined) {
        openSpawnedAgentPane(store, agentId);
      }

      const handled = result['handled'] === true || agentId !== undefined;
      return agentId !== undefined ? { handled, agent_id: agentId } : { handled };
    },

    async spawnPlanner(params: SpawnPlannerParams): Promise<SpawnPlannerResult> {
      const payload: Record<string, unknown> = {
        plan_name: params.planName,
        harness: params.harness,
        model: params.model ?? '',
      };
      if (params.effort != null) {
        payload['effort'] = params.effort;
      }
      const result = await submitCommand(bus, 'planner.spawn', payload);
      const agentId = result['agent_id'] != null ? String(result['agent_id']) : undefined;

      if (agentId !== undefined && store !== undefined) {
        openSpawnedAgentPane(store, agentId);
      }

      const handled = result['handled'] === true || agentId !== undefined;
      return agentId !== undefined ? { handled, agent_id: agentId } : { handled };
    },
  };
}
