/**
 * harnessDisplay — pure display helpers that turn a roster row's raw `harness`/`model` ids into the
 * human labels worn on a transcript pane's bottom border (`Claude Code ◇ Opus 4.8`).
 *
 * Rule 2: formatting lives in a selector, not the component. The transcript pane and any future
 * consumer call these so the harness/model wording never drifts.
 *
 * The harness map covers the five backend harness ids ({@link ../components/spawnWizardMachine.js
 * HARNESS_ORDER}); the model map is built from the spawn wizard's own per-harness label table
 * ({@link ../store/dialogs/harnessModelsActions.js STATIC_HARNESS_MODELS}) so a spawned model id
 * (`opus`, `gpt-5.5`) reads with the SAME label the wizard showed when picking it. Both fall through
 * to a sensible default (title-cased harness / basename model) for anything unmapped, so a new
 * harness or a live-parsed full model string still renders something legible rather than blank.
 */

import { STATIC_HARNESS_MODELS } from '../store/dialogs/harnessModelsActions.js';

/** Backend harness id → display name. The five ids from `HARNESS_ORDER`. */
const HARNESS_LABELS: Readonly<Record<string, string>> = {
  claude_code: 'Claude Code',
  codex: 'Codex',
  cursor: 'Cursor',
  pi: 'Pi',
  antigravity: 'Antigravity',
};

/** Flattened model-id → label map, derived once from every harness's wizard list (so `gpt-5.5` →
 * `GPT-5.5`, `opus` → `Opus`, …). Ids are unique enough across harnesses that a flat map is fine. */
const MODEL_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.values(STATIC_HARNESS_MODELS).flatMap((models) => models.map((m) => [m.id, m.label])),
);

/** Title-case a raw harness token as a last resort (`some_harness` → `Some Harness`). */
function titleCase(raw: string): string {
  return raw
    .split(/[_\s-]+/)
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** The display label for a harness id, or `null` when there is none (so the caller can omit it). */
export function harnessLabel(harness: string | null): string | null {
  const raw = (harness ?? '').trim();
  if (raw === '') {
    return null;
  }
  return HARNESS_LABELS[raw] ?? titleCase(raw);
}

/** The display label for a model id, or `null` when there is none. Strips a `provider/` prefix before
 * the lookup so a full id (`anthropic/opus`) still maps; unmapped ids fall through to the basename. */
export function modelLabel(model: string | null): string | null {
  const raw = (model ?? '').trim();
  if (raw === '' || raw === '—') {
    return null;
  }
  const slash = raw.lastIndexOf('/');
  const base = slash === -1 ? raw : raw.slice(slash + 1);
  return MODEL_LABELS[base] ?? MODEL_LABELS[raw] ?? base;
}

/**
 * The transcript-pane footer string `<harness> ◇ <model>` (the separator passed in so the glyph stays a
 * single source of truth in {@link ../components/glyphs.js}). Returns `null` when NEITHER part is
 * known (so the bottom border draws plain). When only one part is known, just that part is shown.
 */
export function harnessModelFooter(
  harness: string | null,
  model: string | null,
  sep: string,
): string | null {
  const h = harnessLabel(harness);
  const m = modelLabel(model);
  if (h !== null && m !== null) {
    return `${h} ${sep} ${m}`;
  }
  return h ?? m ?? null;
}

/** The marker that separates the repo root from a crow's worktree name in `worktree_path`
 *  (`…/<repo>/.murder/worktrees/<name>`). The display label is just the `<name>` past it. */
const WORKTREE_MARKER = '.murder/worktrees/';

/**
 * The transcript-pane bottom-RIGHT label: the crow's worktree, shown as the bare subdir under
 * `.murder/worktrees/` (e.g. `…/.murder/worktrees/foobar` → `foobar`). A crow with no worktree
 * runs on the main checkout (`worktree_path` is null on the wire — see orchestrator.py), so it
 * reads `main`; a path without the marker also falls back to `main` defensively.
 */
export function worktreeLabel(path: string | null): string {
  const raw = (path ?? '').trim();
  const idx = raw.indexOf(WORKTREE_MARKER);
  if (idx === -1) {
    return 'main';
  }
  const name = raw.slice(idx + WORKTREE_MARKER.length).replace(/\/+$/, '');
  return name === '' ? 'main' : name;
}
