/**
 * harnessDisplay tests — pure label helpers for the transcript-pane bottom border (rule 2). No store, no
 * React: feed raw ids/paths, assert the render-ready labels.
 */

import { worktreeLabel } from '../../src/selectors/harnessDisplay.js';

describe('worktreeLabel — transcript-pane bottom-right', () => {
  it('shows the bare subdir under .murder/worktrees/', () => {
    expect(worktreeLabel('/home/luke/code/murder/.murder/worktrees/foobar')).toBe('foobar');
  });

  it('keeps a nested branch-subdir below the marker', () => {
    expect(worktreeLabel('/repo/.murder/worktrees/feat/login')).toBe('feat/login');
  });

  it('trims a trailing slash off the worktree name', () => {
    expect(worktreeLabel('/repo/.murder/worktrees/foobar/')).toBe('foobar');
  });

  it('reads main when the crow has no worktree (null path)', () => {
    expect(worktreeLabel(null)).toBe('main');
  });

  it('falls back to main for a path without the worktrees marker', () => {
    expect(worktreeLabel('/home/luke/code/murder')).toBe('main');
  });
});
