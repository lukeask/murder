/**
 * `parseWorkflowFire` tests — the pure leading-`:name` workflow-firing parse. Covers a known-workflow
 * fire (with and without args), the builtin/unknown/literal null cases, and the `:name:`-inline
 * exclusion (inline is templates' domain). Mirrors the assertion style of `expandTemplates.test.ts`.
 */

import { describe, expect, it } from 'vitest';
import { parseWorkflowFire } from '@murder/ui-core/input/fireWorkflow.js';

const builtins = new Set<string>(['help', 'save', 'note']);
const workflows = new Set<string>(['wf', 'ship', 'Deploy Production', 'help']); // `help` also collides with a builtin.

const run = (msg: string) => parseWorkflowFire(msg, builtins, workflows);

describe('parseWorkflowFire', () => {
  it('fires a known workflow with the tail as the {input} arg', () => {
    expect(run(':wf do stuff')).toEqual({ name: 'wf', args: { input: 'do stuff' } });
  });

  it('fires a known workflow with NO args when the tail is empty', () => {
    expect(run(':wf')).toEqual({ name: 'wf', args: {} });
  });

  it('trims surrounding whitespace off the {input} arg', () => {
    expect(run(':wf   spaced out   ')).toEqual({ name: 'wf', args: { input: 'spaced out' } });
  });

  it('returns null for a builtin name even when it is also a workflow (builtin wins)', () => {
    expect(run(':help')).toBeNull();
    expect(run(':help me')).toBeNull();
  });

  it('returns null for an unknown name (literal / template fallthrough)', () => {
    expect(run(':bogus arg')).toBeNull();
  });

  it('returns null for non-`:` text', () => {
    expect(run('just a plain message')).toBeNull();
    expect(run('say :wf later')).toBeNull(); // mid-string `:wf` is not leading.
  });

  it('returns null for the inline `:name:` form (double colon is templates domain)', () => {
    expect(run(':wf:')).toBeNull();
    expect(run(':wf: inline')).toBeNull();
  });

  it('fires a quoted human-readable workflow name with the same single input remainder', () => {
    expect(run(':"Deploy Production" ship it')).toEqual({
      name: 'Deploy Production',
      args: { input: 'ship it' },
    });
  });

  it('does not accept malformed or unclosed quoted workflow names', () => {
    expect(run(':"Deploy Production ship it')).toBeNull();
    expect(run(':"Deploy Production":')).toBeNull();
  });
});
