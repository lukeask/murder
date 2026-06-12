/**
 * `spawnWizardMachine` — the **pure** dependent-field brains of the spawn wizard.
 *
 * The wizard mode ({@link ./SpawnWizardModal.js}) is a thin imperative shell over these functions:
 * all the "changing harness recomputes models / effort / which steps are skipped" logic lives here
 * as side-effect-free functions so it is correct *by construction* and unit-testable without
 * rendering an Ink tree. This is the crux of the rewrite (requirement #6/#7).
 *
 * ## Flow order
 *
 *   harness → model → effort → worktree → [branch] → name → [context]
 *
 * - **model** is skipped when the selected harness has no models (static or live snapshot empty).
 * - **effort** is skipped when the harness has no effort enum ({@link effortMatrixFor} → `[]`).
 * - **branch** is inserted only when the worktree selection is "+ new worktree" (a mid-flow runtime
 *   choice — the one step not derivable from harness alone; see {@link stepsFor}'s `newWorktree`).
 * - **context** is shown only when a spawn-context doc was detected at open (reference-by-path).
 */

import type { HarnessModel } from '../store/dialogs/harnessModelsActions.js';
import { modelsFor } from '../store/dialogs/harnessModelsActions.js';

/** The valid backend harness ids, in display order. `claude_code` is the default (NOT `claude`). */
export const HARNESS_ORDER = ['claude_code', 'codex', 'cursor', 'pi', 'antigravity'] as const;

export type HarnessKind = (typeof HARNESS_ORDER)[number];

/** The default harness — the first valid id. Fixes the legacy `'claude'` (invalid) default bug. */
export const DEFAULT_HARNESS: HarnessKind = 'claude_code';

/**
 * Per-harness effort enums + default — a frontend constant mirroring the backend adapter enums.
 * Source of truth (read-only):
 *  - claude_code: claude_code.py:33  `_CC_EFFORT_ORDER = ("low","medium","high","xhigh","max")`
 *  - codex:       codex.py:142       `supported_efforts = ("low","medium","high","xhigh")`
 *  - cursor:      cursor.py:60       `_CURSOR_SPEEDS = ("slow","fast")`
 *  - antigravity / pi: no effort enum (reasoning baked into the model / N/A).
 */
export interface EffortSpec {
  readonly options: readonly string[];
  readonly default: string;
}

const EFFORT_MATRIX: Record<HarnessKind, EffortSpec> = {
  claude_code: { options: ['low', 'medium', 'high', 'xhigh', 'max'], default: 'medium' },
  codex: { options: ['low', 'medium', 'high', 'xhigh'], default: 'medium' },
  cursor: { options: ['slow', 'fast'], default: 'slow' },
  antigravity: { options: [], default: '' },
  pi: { options: [], default: '' },
};

/** Pure: the effort enum + default for a harness. Unknown harness → no effort (skip the step). */
export function effortMatrixFor(harness: string): EffortSpec {
  return EFFORT_MATRIX[harness as HarnessKind] ?? { options: [], default: '' };
}

/** Pure: index of a harness's default effort within its options (0 when none / not found). */
export function defaultEffortCursor(harness: string): number {
  const spec = effortMatrixFor(harness);
  const idx = spec.options.indexOf(spec.default);
  return idx >= 0 ? idx : 0;
}

/** The ordered step ids. `model`/`effort`/`branch`/`context` are conditional. */
export type WizardStep =
  | 'harness'
  | 'model'
  | 'effort'
  | 'worktree'
  | 'branch'
  | 'name'
  | 'context';

/** Inputs that determine which steps are active. */
export interface StepConditions {
  readonly harness: string;
  /** The model map (live snapshot or static fallback) — used to decide if the model step shows. */
  readonly modelMap: Record<string, readonly HarnessModel[]>;
  /** Whether the user selected "+ new worktree" (inserts the branch step). */
  readonly newWorktree: boolean;
  /** Whether a spawn-context doc was detected at open (shows the context step). */
  readonly hasContext: boolean;
}

/**
 * Pure: the ordered list of ACTIVE steps for the given conditions. Recomputed whenever harness /
 * worktree selection changes, so skip-logic is correct by construction. `harness`, `worktree`, and
 * `name` are always present; the rest are conditional.
 */
export function stepsFor(c: StepConditions): WizardStep[] {
  const steps: WizardStep[] = ['harness'];
  if (modelsFor(c.harness, c.modelMap).length > 0) {
    steps.push('model');
  }
  if (effortMatrixFor(c.harness).options.length > 0) {
    steps.push('effort');
  }
  steps.push('worktree');
  if (c.newWorktree) {
    steps.push('branch');
  }
  steps.push('name');
  if (c.hasContext) {
    steps.push('context');
  }
  return steps;
}

/**
 * Pure: the step that follows `current` in the active sequence, or `null` if `current` is the last
 * active step (caller submits). If `current` is no longer active (e.g. it was just skipped by a
 * harness change), returns the first active step as a safe resync.
 */
export function nextStep(current: WizardStep, c: StepConditions): WizardStep | null {
  const steps = stepsFor(c);
  const idx = steps.indexOf(current);
  if (idx < 0) {
    return steps[0] ?? null;
  }
  return steps[idx + 1] ?? null;
}

/** Pure: the 1-based index + total of `current` within the active sequence (for the `n/m` counter). */
export function stepProgress(
  current: WizardStep,
  c: StepConditions,
): { index: number; total: number } {
  const steps = stepsFor(c);
  const idx = steps.indexOf(current);
  return { index: idx >= 0 ? idx + 1 : 1, total: steps.length };
}
