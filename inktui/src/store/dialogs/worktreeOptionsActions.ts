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
 * ## Wire RPC (`worktree.list`)
 *
 * The backend exposes `worktree.list` (host.py) → `list_murder_worktrees_sync`, returning every
 * `.murder/worktrees/*` entry plus the main checkout as `{ ok, entries: [{ path, branch, is_main }] }`.
 * {@link createWorktreeOptionsActions} calls it, drops the main entry (the picker always synthesizes
 * a `main checkout` head), and splices the rest between main and "+ new" via {@link buildWorktreeOptions}.
 * On any rejection the fetch falls back to `[main, +new]` (always-functional: the user can still run
 * on main or spin up a new branch).
 */

import type { BusClient } from '../../bus/BusClient.js';

/**
 * The `worktree.list` read RPC and its reply shape, declared here via TypeScript declaration merging
 * rather than by editing `src/bus/BusClient.ts` (frozen at C1/C2) — the same pattern as
 * {@link ../roster/rosterActions.js}. The registry was designed to be extended a line per method as
 * the service exposes it; declaring it from the consuming slice keeps the bus seam byte-identical
 * while giving `bus.rpc('worktree.list', {})` full type safety.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Enumerate the repo's worktrees (main + `.murder/worktrees/*`). */
    'worktree.list': { params: Record<string, never>; result: WorktreeListReply };
  }
}

/** The `worktree.list` reply, mirroring the service handler (host.py `_worktree_list`). */
export interface WorktreeListReply {
  readonly ok: boolean;
  readonly entries: readonly WorktreeEntryDto[];
}

/** One worktree row as it crosses the wire (Python `list_murder_worktrees_sync`). */
export interface WorktreeEntryDto {
  readonly path: string;
  readonly branch: string | null;
  readonly is_main: boolean;
}

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
    const name = wt.branch ?? wt.path.split('/').filter(Boolean).pop() ?? wt.path;
    options.push({ key: wt.path, label: `${name} (${wt.path})` });
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
   * Fetch the worktree picker options. Always resolves (never throws): pulls existing entries via
   * `worktree.list` and splices them between main and "+ new" via {@link buildWorktreeOptions};
   * a rejection falls back to `[main, +new]`.
   */
  fetch(): Promise<readonly WorktreeOption[]>;
}

/**
 * Build the worktree-options actions bound to one injected {@link BusClient}. `fetch` calls
 * `worktree.list`, drops the main entry (the picker synthesizes its own `main checkout` head) and
 * splices the remaining (non-main) worktrees in via {@link buildWorktreeOptions}.
 */
export function createWorktreeOptionsActions(bus: BusClient): WorktreeOptionsActions {
  return {
    async fetch(): Promise<readonly WorktreeOption[]> {
      try {
        const reply = await bus.rpc('worktree.list', {});
        const existing: ExistingWorktree[] = reply.entries
          .filter((entry) => !entry.is_main)
          .map((entry) => ({
            path: entry.path,
            ...(entry.branch !== null ? { branch: entry.branch } : {}),
          }));
        return buildWorktreeOptions(existing);
      } catch {
        // Never block the wizard: main + new are always available.
        return buildWorktreeOptions([]);
      }
    },
  };
}
