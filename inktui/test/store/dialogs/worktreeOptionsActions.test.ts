/**
 * worktreeOptionsActions tests — worktree picker assembly + wire-payload resolution.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import {
  buildWorktreeOptions,
  createWorktreeOptionsActions,
  MAIN_WORKTREE_KEY,
  NEW_WORKTREE_KEY,
  resolveWorktreePayload,
} from '../../../src/store/dialogs/worktreeOptionsActions.js';

describe('buildWorktreeOptions', () => {
  it('always brackets existing entries with main checkout + "+ new worktree"', () => {
    const opts = buildWorktreeOptions([{ path: '/wt/feat', branch: 'feat' }]);
    expect(opts[0]?.key).toBe(MAIN_WORKTREE_KEY);
    expect(opts.at(-1)?.key).toBe(NEW_WORKTREE_KEY);
    expect(opts[1]).toEqual({ key: '/wt/feat', label: 'feat (/wt/feat)' });
  });

  it('with no existing entries → [main, +new]', () => {
    expect(buildWorktreeOptions([]).map((o) => o.key)).toEqual([
      MAIN_WORKTREE_KEY,
      NEW_WORKTREE_KEY,
    ]);
  });
});

describe('resolveWorktreePayload — wire fields', () => {
  it('main checkout → no worktree fields', () => {
    expect(resolveWorktreePayload(MAIN_WORKTREE_KEY, '')).toEqual({});
    expect(resolveWorktreePayload(null, '')).toEqual({});
  });

  it('existing worktree path → worktreePath', () => {
    expect(resolveWorktreePayload('/wt/feat', '')).toEqual({ worktreePath: '/wt/feat' });
  });

  it('new worktree with branch → worktreeBranch (trimmed)', () => {
    expect(resolveWorktreePayload(NEW_WORKTREE_KEY, '  feature/x  ')).toEqual({
      worktreeBranch: 'feature/x',
    });
  });

  it('new worktree with empty branch → no fields (validation handled upstream)', () => {
    expect(resolveWorktreePayload(NEW_WORKTREE_KEY, '   ')).toEqual({});
  });
});

describe('createWorktreeOptionsActions.fetch', () => {
  it('resolves to [main, +new] today (no worktree RPC yet)', async () => {
    const opts = await createWorktreeOptionsActions(new FakeBusClient()).fetch();
    expect(opts.map((o) => o.key)).toEqual([MAIN_WORKTREE_KEY, NEW_WORKTREE_KEY]);
  });
});
