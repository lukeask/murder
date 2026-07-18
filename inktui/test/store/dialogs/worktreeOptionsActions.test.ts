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
  it('pulls worktrees.list, drops main, splices the rest between main + new', async () => {
    const bus = new FakeBusClient();
    bus.stubQuery('worktrees.list', {
      ok: true,
      entries: [
        { path: '/repo', branch: 'main', is_main: true },
        { path: '/repo/.murder/worktrees/feat', branch: 'feat', is_main: false },
        { path: '/repo/.murder/worktrees/detached', branch: null, is_main: false },
      ],
    });
    const opts = await createWorktreeOptionsActions(bus).fetch();
    expect(opts.map((o) => o.key)).toEqual([
      MAIN_WORKTREE_KEY,
      '/repo/.murder/worktrees/feat',
      '/repo/.murder/worktrees/detached',
      NEW_WORKTREE_KEY,
    ]);
    // Label shows the repo-relative `.murder/worktrees/…` tail; the key stays the absolute path.
    expect(opts[1]?.label).toBe('feat (.murder/worktrees/feat)');
    // A null branch falls back to the path basename for the label (buildWorktreeOptions).
    expect(opts[2]?.label).toBe('detached (.murder/worktrees/detached)');
    expect(bus.queryCalls).toEqual([{ name: 'worktrees.list', params: {} }]);
  });

  it('falls back to [main, +new] when the RPC rejects', async () => {
    const opts = await createWorktreeOptionsActions(new FakeBusClient()).fetch();
    expect(opts.map((o) => o.key)).toEqual([MAIN_WORKTREE_KEY, NEW_WORKTREE_KEY]);
  });
});
