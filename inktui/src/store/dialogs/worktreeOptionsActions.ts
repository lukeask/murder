/**
 * Worktree-options actions — the *only* code that calls the bus for the spawn wizard's worktree
 * list (rule 3).
 *
 * Ports the old Textual wizard's `build_worktree_options` (spawn_wizard.py:71): the picker always
 * offers a **main checkout** at the top and a **"+ new worktree"** at the bottom, with any existing
 * (non-main) worktrees in between. Selecting "+ new worktree" inserts a branch-name text step.
 *
 * ## Wire payload (confirmed against backend — read-only)
 *
 * `Orchestrator.spawn_rogue` (murder/runtime/orchestration/orchestrator.py:480) accepts
 * `worktree_path` (use an existing worktree) XOR `worktree_branch` (create a new named worktree).
 * The wizard threads exactly those snake_case keys into the `crow.spawn_rogue` payload.
 *
 * ## INTEGRATION SWAP POINT (no Ink worktree-list RPC exists yet)
 *
 * There is no `state.*_snapshot` for worktrees on the Ink bus. Until one lands, {@link fetchWorktreeOptions}
 * resolves to just `[main, +new]` (always-functional: the user can always run on main or spin up a
 * new branch). When a worktree-list RPC lands, swap the body of `fetchWorktreeOptions` to pull
 * existing entries and splice them between main and "+ new" via {@link buildWorktreeOptions} — the
 * wizard consumes the returned list verbatim, so no wizard change is needed.
 */

import type { BusClient } from '../../bus/BusClient.js';

/** Sentinel key: run on the repo's main checkout (no worktree threading). */
export const MAIN_WORKTREE_KEY = '__main__';
/** Sentinel key: create a new worktree (inserts the branch-name step). */
export const NEW_WORKTREE_KEY = '__new__';

/** One selectable worktree option. `key` is the sentinel or an existing worktree path. */
export interface WorktreeOption {
  readonly key: string;
  readonly label: string;
}

/** An existing (non-main) worktree as it would cross a future wire — path + optional branch. */
export interface ExistingWorktree {
  readonly path: string;
  readonly branch?: string;
}

/**
 * Pure: assemble the picker list — `[main, ...existing, +new]`. Ports `build_worktree_options`.
 * Kept pure so the worktree step is testable without a bus.
 */
export function buildWorktreeOptions(existing: readonly ExistingWorktree[]): WorktreeOption[] {
  const options: WorktreeOption[] = [{ key: MAIN_WORKTREE_KEY, label: 'main checkout' }];
  for (const wt of existing) {
    const branch = wt.branch ?? wt.path;
    options.push({ key: wt.path, label: `${branch} (${wt.path})` });
  }
  options.push({ key: NEW_WORKTREE_KEY, label: '+ new worktree' });
  return options;
}

/**
 * Pure: resolve a selected worktree key + branch input into the {@link SpawnRogueParams} worktree
 * fields (camelCase — the action maps them to the snake_case wire keys). The single dependent-field
 * derivation for worktrees: main → neither field; an existing path → `worktreePath`; "+ new" →
 * `worktreeBranch`.
 */
export function resolveWorktreePayload(
  key: string | null,
  branch: string,
): { worktreePath?: string; worktreeBranch?: string } {
  if (key === NEW_WORKTREE_KEY) {
    const trimmed = branch.trim();
    return trimmed.length > 0 ? { worktreeBranch: trimmed } : {};
  }
  if (key !== null && key !== MAIN_WORKTREE_KEY) {
    return { worktreePath: key };
  }
  return {};
}

/** The actions exposed to the spawn wizard for the worktree list (rule 3). */
export interface WorktreeOptionsActions {
  /**
   * Fetch the worktree picker options. Always resolves (never throws): `[main, +new]` today; when a
   * worktree-list RPC lands, splice existing entries in via {@link buildWorktreeOptions}.
   */
  fetch(): Promise<readonly WorktreeOption[]>;
}

/**
 * Build the worktree-options actions bound to one injected {@link BusClient}. The bus is unused
 * today (no worktree RPC); it is threaded for the integration swap so the signature is stable.
 */
export function createWorktreeOptionsActions(_bus: BusClient): WorktreeOptionsActions {
  return {
    fetch(): Promise<readonly WorktreeOption[]> {
      // INTEGRATION SWAP: when a worktree-list RPC exists, pull existing entries here and pass them
      // to buildWorktreeOptions. Until then, main + new are always available (functional, no list).
      return Promise.resolve(buildWorktreeOptions([]));
    },
  };
}
