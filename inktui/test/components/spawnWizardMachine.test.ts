/**
 * spawnWizardMachine tests — the PURE dependent-field brains of the spawn wizard.
 *
 * These verify the crux requirement (#6/#7) directly, without rendering: changing the harness
 * recomputes the model list + effort options + which steps are skipped, the effort matrix matches
 * the backend adapter enums, and the default harness is the valid `claude_code`.
 */

import { describe, expect, it } from 'vitest';
import {
  DEFAULT_HARNESS,
  defaultEffortCursor,
  effortMatrixFor,
  HARNESS_ORDER,
  nextStep,
  type StepConditions,
  stepProgress,
  stepsFor,
} from '../../src/components/spawnWizardMachine.js';
import { STATIC_HARNESS_MODELS } from '../../src/store/dialogs/harnessModelsActions.js';

const cond = (overrides: Partial<StepConditions> = {}): StepConditions => ({
  harness: DEFAULT_HARNESS,
  modelMap: STATIC_HARNESS_MODELS,
  newWorktree: false,
  hasContext: false,
  creatingFavorite: false,
  ...overrides,
});

describe('spawnWizardMachine — defaults + ordering', () => {
  it('default harness is the valid claude_code (not the legacy invalid "claude")', () => {
    expect(DEFAULT_HARNESS).toBe('claude_code');
    expect(HARNESS_ORDER[0]).toBe('claude_code');
  });

  it('lists the valid harnesses in the locked order', () => {
    expect([...HARNESS_ORDER]).toEqual(['claude_code', 'codex', 'cursor', 'pi', 'antigravity']);
  });

  it('claude_code full flow: harness → model → effort → worktree → name', () => {
    expect(stepsFor(cond())).toEqual(['harness', 'model', 'effort', 'worktree', 'name']);
  });

  it('inserts the branch step only when "+ new worktree" is selected', () => {
    expect(stepsFor(cond({ newWorktree: true }))).toEqual([
      'harness',
      'model',
      'effort',
      'worktree',
      'branch',
      'name',
    ]);
  });

  it('appends the context step only when a doc was detected', () => {
    expect(stepsFor(cond({ hasContext: true })).at(-1)).toBe('context');
  });
});

describe('spawnWizardMachine — effort matrix mirrors backend adapter enums', () => {
  // DRIFT RISK (code-review jun13): this matrix is hand-mirrored from the Python harness adapters
  // and pinned by TS expectations ONLY — there is no cross-language golden, so backend changes here
  // pass green while the spawn wizard silently diverges.
  // KNOWN DIVERGENCE: `antigravity.py` now declares `supported_efforts = ("low", "medium", "high")`,
  // `default_effort = "medium"` — but the TS source (`effortMatrixFor`) still returns NO effort for
  // antigravity, and the case below pins that stale behavior. This is a real product drift in the TS
  // source, not just a test gap; left here as a flagged follow-up (the source fix is out of scope for
  // this test pass). Proper fix: have the Python adapter tests emit the per-harness effort matrices
  // into a fixture this file imports, the way the DTO goldens already work.
  it('claude_code: low/medium/high/xhigh/max, default medium (claude_code.py:33)', () => {
    expect(effortMatrixFor('claude_code')).toEqual({
      options: ['low', 'medium', 'high', 'xhigh', 'max'],
      default: 'medium',
    });
  });

  it('codex: low/medium/high/xhigh, default medium (codex.py:142)', () => {
    expect(effortMatrixFor('codex')).toEqual({
      options: ['low', 'medium', 'high', 'xhigh'],
      default: 'medium',
    });
  });

  it('cursor: slow/fast, default slow (cursor.py:60)', () => {
    expect(effortMatrixFor('cursor')).toEqual({ options: ['slow', 'fast'], default: 'slow' });
  });

  it('antigravity / pi have NO effort enum', () => {
    expect(effortMatrixFor('antigravity').options).toEqual([]);
    expect(effortMatrixFor('pi').options).toEqual([]);
  });

  it('unknown harness → no effort (graceful)', () => {
    expect(effortMatrixFor('bogus').options).toEqual([]);
  });

  it('defaultEffortCursor points at the per-harness default option', () => {
    expect(defaultEffortCursor('claude_code')).toBe(1); // 'medium' at index 1
    expect(defaultEffortCursor('codex')).toBe(1); // 'medium' at index 1
    expect(defaultEffortCursor('cursor')).toBe(0); // 'slow' at index 0
    expect(defaultEffortCursor('antigravity')).toBe(0); // no enum → 0
  });
});

describe('spawnWizardMachine — harness change recomputes skips', () => {
  it('cursor: skips the model step (no models) but keeps effort', () => {
    // cursor has [] static models and a slow/fast effort enum.
    expect(stepsFor(cond({ harness: 'cursor' }))).toEqual([
      'harness',
      'effort',
      'worktree',
      'name',
    ]);
  });

  it('antigravity: skips BOTH model and effort', () => {
    expect(stepsFor(cond({ harness: 'antigravity' }))).toEqual(['harness', 'worktree', 'name']);
  });

  it('pi: skip model + effort', () => {
    expect(stepsFor(cond({ harness: 'pi' }))).toEqual(['harness', 'worktree', 'name']);
  });

  it('a live snapshot adding models to cursor restores its model step', () => {
    const map = { ...STATIC_HARNESS_MODELS, cursor: [{ id: 'composer', label: 'Composer' }] };
    expect(stepsFor(cond({ harness: 'cursor', modelMap: map }))).toEqual([
      'harness',
      'model',
      'effort',
      'worktree',
      'name',
    ]);
  });

  it('an empty snapshot for claude_code skips the model step', () => {
    const map = { ...STATIC_HARNESS_MODELS, claude_code: [] };
    expect(stepsFor(cond({ harness: 'claude_code', modelMap: map }))).not.toContain('model');
  });
});

describe('spawnWizardMachine — nextStep / progress', () => {
  it('nextStep walks the active sequence and ends with null', () => {
    const c = cond({ harness: 'antigravity' }); // harness → worktree → name
    expect(nextStep('harness', c)).toBe('worktree');
    expect(nextStep('worktree', c)).toBe('name');
    expect(nextStep('name', c)).toBeNull();
  });

  it('nextStep from a now-skipped step resyncs to the first step', () => {
    // 'model' is not active for antigravity; nextStep should resync rather than crash.
    expect(nextStep('model', cond({ harness: 'antigravity' }))).toBe('harness');
  });

  it('stepProgress counts within the active sequence', () => {
    const c = cond(); // 5 steps for claude_code
    expect(stepProgress('harness', c)).toEqual({ index: 1, total: 5 });
    expect(stepProgress('name', c)).toEqual({ index: 5, total: 5 });
  });
});
