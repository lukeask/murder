/**
 * `SpawnWizardModal` — the `ctrl+s` spawn wizard: a **multi-step modal C7M mode** that collects a
 * full rogue-crow spec (harness → model → effort → worktree → [branch] → name → [context]), then
 * fires `crow.spawn_rogue` through the `command.submit` choke point.
 *
 * ## Flow (dependent fields)
 *
 *   harness → model → effort → worktree → [branch] → name → [context]
 *
 * The brains of "which steps are active / what options each step has" is the **pure** machine in
 * {@link ./spawnWizardMachine.js}. This file is a thin imperative shell: it tracks selections in
 * closure state, asks the machine what step comes next, and renders the active step. Because the
 * derivation is pure, changing the harness mid-flow recomputes the model list + effort options +
 * which steps are skipped *by construction* — there is no transition table to keep in sync.
 *
 *  1. **harness** — the valid backend harness ids ({@link HARNESS_ORDER}), default `claude_code`
 *     (fixes the legacy `'claude'` invalid-default bug). Selecting a harness recomputes downstream.
 *  2. **model** (conditional) — driven by the pull-only `state.harness_models_snapshot` RPC
 *     ({@link ../store/dialogs/harnessModelsActions.js}), fetched once on open with a static
 *     last-good fallback. Skipped when the harness has no models.
 *  3. **effort** (conditional) — the per-harness effort enum ({@link effortMatrixFor}). Skipped for
 *     `antigravity` / `pi` (no effort enum).
 *  4. **worktree** — main checkout / existing worktrees / "+ new worktree"
 *     ({@link ../store/dialogs/worktreeOptionsActions.js}).
 *  5. **branch** (conditional) — a non-empty branch-name text field, inserted only after "+ new
 *     worktree".
 *  6. **name** — the rogue name (blank = autogenerate).
 *  7. **context** (conditional) — reference-by-path include-doc step, only when a doc was focused at
 *     open. Accepting builds `"Please read <path> before starting."`, delivered out-of-band.
 *
 * ## Input model (keymap vs. onUncaptured)
 *
 * The wizard mixes list steps (want `j`/`k`) with text steps (want literal letters). Following the
 * `NewPlanModal` house pattern, the keymap holds ONLY structural keys (arrows, return, backspace,
 * ctrl+u, escape) and a per-step printable router lives in `onUncaptured`:
 *  - list steps: `j`→down, `k`→up; other letters swallowed.
 *  - text steps (branch/name): insert the char.
 *  - context step: `y`→accept+submit, `n`→decline+submit.
 *
 * ## Spawn context — reference-by-path (locked mechanism)
 *
 * Accepting the context doc builds `"Please read ${spawnContext.path} before starting."` — telling
 * the rogue to *read* the doc (priming engagement), not inlining its body. The kickoff rides
 * out-of-band as a separate `agent.message` (the spawn handler ignores kickoff fields). See
 * {@link ../store/dialogs/spawnActions.js}.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useModalWidth } from '../hooks/useTerminalSize.js';
import type { Modifier } from '../input/bindings.js';
import type { Mode, ModeHint, ModeStoreApi } from '../input/modeStore.js';
import type { HarnessModel, HarnessModelsActions } from '../store/dialogs/harnessModelsActions.js';
import { modelsFor, STATIC_HARNESS_MODELS } from '../store/dialogs/harnessModelsActions.js';
import type { SpawnActions } from '../store/dialogs/spawnActions.js';
import type {
  SpawnFavorite,
  SpawnFavoritesActions,
} from '../store/dialogs/spawnFavoritesActions.js';
import type {
  WorktreeOption,
  WorktreeOptionsActions,
} from '../store/dialogs/worktreeOptionsActions.js';
import {
  buildWorktreeOptions,
  NEW_WORKTREE_KEY,
  resolveWorktreePayload,
} from '../store/dialogs/worktreeOptionsActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import {
  DEFAULT_HARNESS,
  defaultEffortCursor,
  effortMatrixFor,
  HARNESS_ORDER,
  nextStep,
  type StepConditions,
  stepProgress,
  type WizardStep,
} from './spawnWizardMachine.js';
import { deleteLastChar, insertChar, TextInput } from './TextInput.js';

// Bring the dispatcher's `onUncaptured` augmentation into scope (text/list steps need it).
import '../input/dispatcher.js';

/** Intent union — structural-key actions only. Printable chars (j/k/y/n + text) go through
 * `onUncaptured`, not the keymap. */
type SpawnWizardIntent =
  | 'cursorUp'
  | 'cursorDown'
  | 'cursorLeft'
  | 'cursorRight'
  | 'confirm'
  | 'backspace'
  | 'deleteAll'
  | 'dismiss';

/** A document that can be included as spawn context (reference-by-path). */
export interface SpawnContext {
  /** The display title of the doc (e.g. `'my-plan'`). */
  readonly title: string;
  /** The `.murder/`-relative path to read (e.g. `.murder/plans/my-plan.md`). */
  readonly path: string;
}

/**
 * Legacy default exports retained for back-compat with the call site / older imports. The wizard no
 * longer *needs* a hardcoded harness/model (it has a picker), but the harness default is now the
 * VALID `claude_code` id — the old `'claude'` was an invalid backend id (the bug this rewrite fixes).
 */
export const DEFAULT_SPAWN_HARNESS = DEFAULT_HARNESS;
export const DEFAULT_SPAWN_MODEL = 'sonnet';

/** Options passed to the spawn wizard mode factory. */
export interface SpawnWizardModeOptions {
  /**
   * The pull-only model-snapshot actions. Optional so existing call sites compile; when absent the
   * wizard uses the static last-good map only (no live fetch). The `spawn` handler in `App.tsx`
   * should pass `createHarnessModelsActions(bus)`.
   */
  readonly modelActions?: HarnessModelsActions;
  /**
   * The worktree-options actions. Optional; when absent the wizard offers `[main, +new]` only
   * (always functional). The `spawn` handler should pass `createWorktreeOptionsActions(bus)`.
   */
  readonly worktreeActions?: WorktreeOptionsActions;
  /** The focused doc, if detected at `ctrl+s` time → shows the context step (reference-by-path). */
  readonly spawnContext?: SpawnContext | null;
  /** Called after a successful spawn with the chosen effort + kickoff message (fired after exit). */
  readonly onSubmit?: (effort: string, kickoffMessage: string | null) => void;
  /** Called when dismissed without spawning. */
  readonly onDismiss?: () => void;
  /**
   * The enabled harnesses for the left column. Defaults to {@link HARNESS_ORDER}. The `spawn`
   * handler passes `settings.effectiveCrowHarnesses`.
   */
  readonly enabledHarnesses?: readonly string[];
  /**
   * The spawn-favorites persistence actions. When present, favorites load on open and
   * create/delete/rename persist via the bus. When absent, the right column is just `create
   * favorite` (no saved entries), and saves are no-ops.
   */
  readonly favoriteActions?: SpawnFavoritesActions;
  /**
   * Decides whether a `[0-9]` digit means the RIGHT (favorites) column (command-modified) vs the
   * LEFT (harness) column (bare). Defaults to `(k) => k.ctrl || k.meta`. The `spawn` handler passes
   * `bindings.resolved.isCommandModified`.
   */
  readonly commandModified?: (key: Key) => boolean;
  /**
   * The configured command modifier — used ONLY for the right-column chord LABEL prefix
   * (`'alt'` → `A`, anything else → `C`). Defaults to `'C'`.
   */
  readonly commandModifier?: Modifier;
}

/** The default effort options — exported for back-compat with the old test/import surface. */
export const DEFAULT_EFFORT_OPTIONS: readonly string[] = effortMatrixFor(DEFAULT_HARNESS).options;

/** The stable mode id for idempotent re-enter. */
export const SPAWN_WIZARD_MODE_ID = 'spawn-wizard';

/** Mutable closure state — not React state. Mutated by `onIntent`/`onUncaptured`; `render` reads it. */
interface SpawnWizardState {
  step: WizardStep;
  /** The fetched (or static) per-harness model map; replaced when the live snapshot lands. */
  modelMap: Record<string, readonly HarnessModel[]>;
  /** The worktree picker options (resolved on open). */
  worktreeOptions: readonly WorktreeOption[];
  /** The enabled harnesses for the left column (seeded from opts). */
  enabledHarnesses: readonly string[];
  /** The saved spawn favorites (right column). Filled async on open, like models/worktrees. */
  favorites: SpawnFavorite[];
  /** Prevent the initial empty array from masquerading as an authoritative empty saved list. */
  favoritesStatus: 'loading' | 'ready';
  // Selections.
  harness: string;
  model: string;
  effort: string;
  worktreeKey: string | null;
  branch: string;
  name: string;
  contextAccepted: boolean;
  // List cursors (per selection step).
  harnessCursor: number;
  modelCursor: number;
  effortCursor: number;
  worktreeCursor: number;
  // First-step two-column grid state.
  /** Which column the harness step has focus on (left = harness, right = favorites). */
  focusedColumn: 'harness' | 'favorites';
  /** The cursor within the right (favorites + create-row) column. */
  favoriteCursor: number;
  /** True once the user chose `create favorite` — appends the `nameFavorite` step + saves on submit. */
  creatingFavorite: boolean;
  /** The favorite name being typed on the `nameFavorite` step. */
  favoriteName: string;
  /** The first-step sub-mode: normal nav, delete-confirm, or inline rename. */
  gridMode: 'nav' | 'confirmDelete' | 'rename';
  /** The in-progress rename value (when `gridMode === 'rename'`). */
  renameValue: string;
  /** The right-column chord label prefix (`'A'` for alt, else `'C'`) — display only. */
  chordPrefix: string;
  error: string | null;
}

/**
 * The bottom-bar hints for the active wizard step (item 4b/4c — hints moved out of the modal box into
 * the bottom bar). List steps advertise nav + confirm + cancel; text steps drop nav; the context step
 * advertises its yes/no nav (item 7). Pure over the step so it tests without the bar.
 */
export function spawnWizardHints(
  step: WizardStep,
  ctx?: { favoritesFocused?: boolean },
): readonly ModeHint[] {
  const cancel: ModeHint = { key: 'esc', description: 'cancel' };
  switch (step) {
    case 'harness':
      return [
        { key: 'h/l', description: 'cols' },
        { key: 'j/k', description: 'nav' },
        { key: '[n]', description: 'select' },
        { key: 'enter', description: 'confirm' },
        ...(ctx?.favoritesFocused === true ? [{ key: 'd/r', description: 'del/rename' }] : []),
        cancel,
      ];
    case 'model':
    case 'effort':
    case 'worktree':
      return [
        { key: 'j/k', description: 'nav' },
        { key: '[n]', description: 'select' },
        { key: 'enter', description: 'confirm' },
        cancel,
      ];
    case 'branch':
    case 'name':
    case 'nameFavorite':
      return [{ key: 'enter', description: 'confirm' }, cancel];
    case 'context':
      return [
        { key: 'h/l', description: 'nav' },
        { key: 'enter', description: 'confirm' },
        { key: 'y/n', description: 'include/skip' },
        cancel,
      ];
    default:
      return [cancel];
  }
}

/** Build the {@link StepConditions} from current closure state — the input to the pure machine. */
function conditions(s: SpawnWizardState, hasContext: boolean): StepConditions {
  return {
    harness: s.harness,
    model: s.model,
    modelMap: s.modelMap,
    newWorktree: s.worktreeKey === NEW_WORKTREE_KEY,
    hasContext,
    creatingFavorite: s.creatingFavorite,
  };
}

/**
 * Build the spawn wizard {@link Mode}. Enter via:
 * `modes.getState().enter(spawnWizardMode(modes, actions, opts))`.
 */
export function spawnWizardMode(
  modes: ModeStoreApi,
  actions: SpawnActions,
  opts: SpawnWizardModeOptions = {},
): Mode<SpawnWizardIntent> {
  const id = SPAWN_WIZARD_MODE_ID;
  const spawnContext = opts.spawnContext ?? null;
  const hasContext = spawnContext !== null;

  const enabledHarnesses =
    opts.enabledHarnesses !== undefined && opts.enabledHarnesses.length > 0
      ? opts.enabledHarnesses
      : [...HARNESS_ORDER];
  const commandModified = opts.commandModified ?? ((k: Key) => k.ctrl === true || k.meta === true);
  const chordPrefix = opts.commandModifier === 'alt' ? 'A' : 'C';

  // Mutable local state. Start on the harness step with the first enabled harness preselected.
  const initialHarness = enabledHarnesses[0] ?? DEFAULT_HARNESS;
  const s: SpawnWizardState = {
    step: 'harness',
    modelMap: STATIC_HARNESS_MODELS,
    worktreeOptions: buildWorktreeOptions([]),
    enabledHarnesses,
    favorites: [],
    favoritesStatus: opts.favoriteActions === undefined ? 'ready' : 'loading',
    harness: initialHarness,
    model: '',
    effort: '',
    worktreeKey: null,
    branch: '',
    name: '',
    contextAccepted: true,
    harnessCursor: 0,
    modelCursor: 0,
    effortCursor: defaultEffortCursor(initialHarness),
    worktreeCursor: 0,
    focusedColumn: 'harness',
    favoriteCursor: 0,
    creatingFavorite: false,
    favoriteName: '',
    gridMode: 'nav',
    renameValue: '',
    chordPrefix,
    error: null,
  };

  function refresh(): void {
    // Async loads belong to this exact wizard instance. An older, dismissed
    // instance must not re-enter a newer wizard that happens to share its id.
    const current = modes.getState().stack.find((f) => f.mode === mode);
    if (current !== undefined) {
      modes.getState().enter(mode);
    }
  }

  // Fetch the live model snapshot + worktree options once on open. Both fall back gracefully, so a
  // rejection just leaves the static / `[main, +new]` defaults already in state.
  if (opts.modelActions !== undefined) {
    void opts.modelActions
      .fetch()
      .then((map) => {
        s.modelMap = map;
        refresh();
      })
      .catch(() => {});
  }
  if (opts.worktreeActions !== undefined) {
    void opts.worktreeActions
      .fetch()
      .then((options) => {
        s.worktreeOptions = options;
        refresh();
      })
      .catch(() => {});
  }
  if (opts.favoriteActions !== undefined) {
    void opts.favoriteActions
      .load()
      .then((f) => {
        s.favorites = f;
        s.favoritesStatus = 'ready';
        refresh();
      })
      .catch(() => {
        s.favoritesStatus = 'ready';
        refresh();
      });
  }

  /** The number of selectable rows on the active selection step (for cursor wrapping). */
  /** Push an RPC-rejection toast (same shape as `doSubmit`'s catch). */
  function toast(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
  }

  // --- Right-column (favorites) model ------------------------------------------------------------

  /** The number of rows in the right column: favorites + the trailing `create favorite` row (hidden
   * at 10 favorites). */
  function rightRowCount(): number {
    if (s.favoritesStatus === 'loading') return 0;
    return s.favorites.length < 10 ? s.favorites.length + 1 : 10;
  }

  /** True when right-column index `i` is the `create favorite` row. */
  function isCreateRow(i: number): boolean {
    return i === s.favorites.length && s.favorites.length < 10;
  }

  function currentListLength(): number {
    switch (s.step) {
      case 'harness':
        return s.enabledHarnesses.length;
      case 'model':
        return modelsFor(s.harness, s.modelMap).length;
      case 'effort':
        return effortMatrixFor(s.harness, s.model).options.length;
      case 'worktree':
        return s.worktreeOptions.length;
      default:
        return 0;
    }
  }

  function cursorFor(step: WizardStep): number {
    switch (step) {
      case 'harness':
        return s.harnessCursor;
      case 'model':
        return s.modelCursor;
      case 'effort':
        return s.effortCursor;
      case 'worktree':
        return s.worktreeCursor;
      default:
        return 0;
    }
  }

  function setCursor(step: WizardStep, value: number): void {
    switch (step) {
      case 'harness':
        s.harnessCursor = value;
        break;
      case 'model':
        s.modelCursor = value;
        break;
      case 'effort':
        s.effortCursor = value;
        break;
      case 'worktree':
        s.worktreeCursor = value;
        break;
      default:
        break;
    }
  }

  function moveCursor(delta: number): void {
    const len = currentListLength();
    if (len > 0) {
      setCursor(s.step, (cursorFor(s.step) + delta + len) % len);
      refresh();
    }
  }

  /** Recompute downstream selections when the harness changes — the dependent-field reset. Resets
   * the model selection + cursor and seeds the per-harness default effort. */
  function onHarnessChanged(): void {
    s.harness = s.enabledHarnesses[s.harnessCursor] ?? DEFAULT_HARNESS;
    s.model = '';
    s.modelCursor = 0;
    s.effort = '';
    s.effortCursor = defaultEffortCursor(s.harness);
  }

  /** Move within the focused column on the harness step (wrapping by that column's length). */
  function moveWithinColumn(delta: number): void {
    if (s.focusedColumn === 'harness') {
      const len = s.enabledHarnesses.length;
      if (len > 0) s.harnessCursor = (s.harnessCursor + delta + len) % len;
    } else {
      const len = rightRowCount();
      if (len > 0) s.favoriteCursor = (s.favoriteCursor + delta + len) % len;
    }
    refresh();
  }

  /** Switch the harness step's focused column, clamping the destination cursor into range. */
  function switchColumn(target: 'harness' | 'favorites'): void {
    if (s.focusedColumn === target) return;
    s.focusedColumn = target;
    if (target === 'harness') {
      const len = s.enabledHarnesses.length;
      if (s.harnessCursor >= len) s.harnessCursor = Math.max(0, len - 1);
    } else {
      const len = rightRowCount();
      if (s.favoriteCursor >= len) s.favoriteCursor = Math.max(0, len - 1);
    }
    refresh();
  }

  /** Map a `'0'..'9'` digit to a right-column index (`1..9` → `0..8`, `0` → `9`). */
  function rightDigitIndex(digit: string): number {
    return digit === '0' ? 9 : digit.charCodeAt(0) - '1'.charCodeAt(0);
  }

  /** Act on the right-column entry at index `i`: create-row → start the create-favorite flow; a real
   * favorite → prefill harness/model/effort and jump to the worktree step. */
  function actOnFavoriteRow(i: number): void {
    if (isCreateRow(i)) {
      s.creatingFavorite = true;
      advance();
      return;
    }
    const f = s.favorites[i];
    if (f === undefined) return;
    s.harness = f.harness;
    s.model = f.model;
    s.effort = f.effort;
    s.creatingFavorite = false;
    s.step = 'worktree';
    refresh();
  }

  /** Capture the current step's selection, then advance to the next active step (or submit). */
  function advance(): void {
    switch (s.step) {
      case 'harness':
        onHarnessChanged();
        break;
      case 'model':
        s.model = modelsFor(s.harness, s.modelMap)[s.modelCursor]?.id ?? '';
        s.effort = '';
        s.effortCursor = defaultEffortCursor(s.harness, s.model);
        break;
      case 'effort':
        s.effort = effortMatrixFor(s.harness, s.model).options[s.effortCursor] ?? '';
        break;
      case 'worktree':
        s.worktreeKey = s.worktreeOptions[s.worktreeCursor]?.key ?? null;
        break;
      case 'branch':
        if (s.branch.trim().length === 0) {
          s.error = 'Branch name is required.';
          refresh();
          return;
        }
        break;
      case 'nameFavorite':
        if (s.favoriteName.trim().length === 0) {
          s.error = 'Favorite name is required.';
          refresh();
          return;
        }
        break;
      default:
        break;
    }
    s.error = null;

    const next = nextStep(s.step, conditions(s, hasContext));
    if (next === null) {
      doSubmit();
      return;
    }
    s.step = next;
    refresh();
  }

  /** Build the kickoff message for reference-by-path when context is accepted. */
  function buildKickoffMessage(): string | null {
    if (spawnContext === null || !s.contextAccepted) {
      return null;
    }
    return `Please read ${spawnContext.path} before starting.`;
  }

  /** Submit: exit-then-act (restore focus first, then fire the async RPC). */
  function doSubmit(): void {
    modes.getState().exit(id);
    const effort = effortMatrixFor(s.harness, s.model).options.length > 0 ? s.effort : '';
    // The model is the picker selection, or `''` for harnesses that skip the model step (cursor /
    // antigravity / pi). The live handler requires a STRING but tolerates the
    // empty one (`model.strip() or None` → the adapter picks its own default), so we must NOT force
    // a Claude id like 'sonnet' onto a non-Claude harness — that is the same invalid-id bug class
    // this rewrite fixes for the harness field. See orchestrator.py:573 (isinstance str) + :504.
    const model = s.model;
    const kickoffMessage = buildKickoffMessage();
    const wt = resolveWorktreePayload(s.worktreeKey, s.branch);
    const name = s.name.trim();
    // When creating a favorite, persist it alongside the spawn (fire-and-forget; a save rejection
    // toasts but never blocks the spawn). Clamp to 10.
    if (s.creatingFavorite && opts.favoriteActions !== undefined) {
      const next = [
        ...s.favorites,
        { name: s.favoriteName.trim(), harness: s.harness, model, effort },
      ].slice(0, 10);
      void opts.favoriteActions.save(next).catch((e: unknown) => toast(e));
    }
    void actions
      .spawnRogue({
        harness: s.harness,
        model,
        ...(effort !== '' ? { effort } : {}),
        ...(name !== '' ? { name } : {}),
        ...wt,
        kickoffMessage,
      })
      .then(() => {
        opts.onSubmit?.(effort, kickoffMessage);
      })
      .catch((error: unknown) => {
        // Exit-then-act: the wizard is already gone and focus restored, so an inline error has
        // nowhere to render. Surface the action-level RPC rejection on the global toastStore (a
        // singleton, independent of this unmounted wizard's lifecycle), using the structured
        // `rpc error [code]: message` text from UdsBusClient's rejection.
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  }

  const mode: Mode<SpawnWizardIntent> = {
    id,
    presentation: 'modal',
    // Item 4b/4c: the step's hints live in the bottom bar, not inside the modal box. A getter so the
    // bar (which re-reads on every mode-stack change — `refresh()` re-enters the frame) always shows
    // the CURRENT step's hints.
    get hints(): readonly ModeHint[] {
      return spawnWizardHints(s.step, {
        favoritesFocused: s.step === 'harness' && s.focusedColumn === 'favorites',
      });
    },
    // Structural keys only — printable chars (j/k/y/n + free text) ride `onUncaptured`.
    keymap: [
      { chord: { key: { downArrow: true } }, intent: 'cursorDown', description: 'next option' },
      { chord: { key: { upArrow: true } }, intent: 'cursorUp', description: 'prev option' },
      // Item 7: ←/→ move the context step's yes/no highlight (h/l ride onUncaptured).
      { chord: { key: { leftArrow: true } }, intent: 'cursorLeft', description: 'prev choice' },
      { chord: { key: { rightArrow: true } }, intent: 'cursorRight', description: 'next choice' },
      { chord: { key: { return: true } }, intent: 'confirm', description: 'confirm' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { input: 'u', key: { meta: true } }, intent: 'deleteAll', description: 'clear' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      // The harness step's rename sub-mode is a text field over `renameValue` — handle it BEFORE
      // normal routing so structural keys edit/commit/cancel the rename, not the wizard.
      if (s.step === 'harness' && s.gridMode === 'rename') {
        switch (intent) {
          case 'backspace':
            s.renameValue = deleteLastChar(s.renameValue);
            refresh();
            return;
          case 'deleteAll':
            s.renameValue = '';
            refresh();
            return;
          case 'confirm': {
            const trimmed = s.renameValue.trim();
            if (trimmed.length > 0) {
              const cur = s.favorites[s.favoriteCursor];
              if (cur !== undefined) {
                s.favorites[s.favoriteCursor] = { ...cur, name: trimmed };
                void opts.favoriteActions?.save(s.favorites).catch((e: unknown) => toast(e));
              }
              s.gridMode = 'nav';
              refresh();
            }
            return;
          }
          case 'dismiss':
            // Cancel the sub-mode only — do NOT exit the modal.
            s.gridMode = 'nav';
            refresh();
            return;
          default:
            // Arrows etc. are inert while renaming.
            return;
        }
      }

      const isList =
        s.step === 'harness' || s.step === 'model' || s.step === 'effort' || s.step === 'worktree';
      const isText = s.step === 'branch' || s.step === 'name' || s.step === 'nameFavorite';
      switch (intent) {
        case 'cursorUp':
          if (s.step === 'harness') moveWithinColumn(-1);
          else if (isList) moveCursor(-1);
          break;
        case 'cursorDown':
          if (s.step === 'harness') moveWithinColumn(1);
          else if (isList) moveCursor(1);
          break;
        case 'cursorLeft':
          if (s.step === 'harness') {
            switchColumn('harness');
          } else if (s.step === 'context' && !s.contextAccepted) {
            // Item 7: the context step's yes/no is a two-cell radio; ← highlights "yes".
            s.contextAccepted = true;
            refresh();
          }
          break;
        case 'cursorRight':
          if (s.step === 'harness') {
            switchColumn('favorites');
          } else if (s.step === 'context' && s.contextAccepted) {
            s.contextAccepted = false;
            refresh();
          }
          break;
        case 'confirm':
          if (s.step === 'harness' && s.focusedColumn === 'favorites') {
            actOnFavoriteRow(s.favoriteCursor);
          } else {
            advance();
          }
          break;
        case 'backspace':
          if (isText) {
            if (s.step === 'branch') s.branch = deleteLastChar(s.branch);
            else if (s.step === 'name') s.name = deleteLastChar(s.name);
            else s.favoriteName = deleteLastChar(s.favoriteName);
            refresh();
          }
          break;
        case 'deleteAll':
          if (isText) {
            if (s.step === 'branch') s.branch = '';
            else if (s.step === 'name') s.name = '';
            else s.favoriteName = '';
            refresh();
          }
          break;
        case 'dismiss':
          // A non-nav grid sub-mode cancels first; only `nav` exits the modal.
          if (s.gridMode !== 'nav') {
            s.gridMode = 'nav';
            refresh();
            break;
          }
          modes.getState().exit(id);
          opts.onDismiss?.();
          break;
        default:
          return intent satisfies never;
      }
    },
    // Per-step printable router (see the class doc): list steps map j/k; text steps insert; context
    // step maps y/n. The dispatcher calls this only when the keymap produced no match.
    onUncaptured(input: string, key: Key): boolean {
      if (input.length === 0 || key.escape || key.return) {
        return false;
      }

      // First-step grid sub-modes — handled BEFORE the digit / ctrl-meta logic so a single keypress
      // confirms a delete, edits a rename, or cancels.
      if (s.step === 'harness' && s.gridMode === 'confirmDelete') {
        if (input === 'd' && !key.ctrl && !key.meta) {
          s.favorites.splice(s.favoriteCursor, 1);
          s.favoriteCursor = Math.max(0, Math.min(s.favoriteCursor, rightRowCount() - 1));
          s.gridMode = 'nav';
          void opts.favoriteActions?.save(s.favorites).catch((e: unknown) => toast(e));
          refresh();
          return true;
        }
        // Any other key cancels.
        s.gridMode = 'nav';
        refresh();
        return true;
      }
      if (s.step === 'harness' && s.gridMode === 'rename') {
        // Printable (non-ctrl/meta) chars extend the rename; structural keys ride onIntent.
        if (!key.ctrl && !key.meta) {
          s.renameValue = insertChar(s.renameValue, input);
          refresh();
          return true;
        }
        return true; // swallow ctrl/meta while renaming
      }

      // Digit select+advance — runs BEFORE the ctrl/meta bail because command-modified digits arrive
      // with `key.ctrl`/`key.meta` set and still come through onUncaptured (no keymap match). Only the
      // harness/model/effort/worktree list steps intercept digits; text steps must keep them.
      if (/^[0-9]$/.test(input)) {
        if (s.step === 'harness') {
          const cmd = commandModified(key);
          if (cmd) {
            // Right column (favorites).
            const idx = rightDigitIndex(input);
            if (idx >= 0 && idx < rightRowCount()) {
              actOnFavoriteRow(idx);
            }
          } else {
            // Left column (harness) — '0' is NOT a left selection (only 1..9).
            if (input !== '0') {
              const idx = input.charCodeAt(0) - '1'.charCodeAt(0);
              if (idx < s.enabledHarnesses.length) {
                s.harnessCursor = idx;
                advance();
              }
            }
          }
          return true; // digits never leak on the harness step
        }
        if (s.step === 'model' || s.step === 'effort' || s.step === 'worktree') {
          // Bare digit selects + advances; a command-modified digit is a swallowed no-op here.
          if (!commandModified(key) && input !== '0') {
            const idx = input.charCodeAt(0) - '1'.charCodeAt(0);
            if (idx < currentListLength()) {
              setCursor(s.step, idx);
              advance();
            }
          }
          return true;
        }
        // Text/context steps: fall through (favoriteName/branch/name accept digit chars).
      }

      if (key.ctrl || key.meta) {
        return false;
      }
      switch (s.step) {
        case 'harness':
          // Column nav (j/k within column, h/l switch columns) + d/r sub-mode triggers.
          if (input === 'j') {
            moveWithinColumn(1);
            return true;
          }
          if (input === 'k') {
            moveWithinColumn(-1);
            return true;
          }
          if (input === 'h') {
            switchColumn('harness');
            return true;
          }
          if (input === 'l') {
            switchColumn('favorites');
            return true;
          }
          if (
            s.focusedColumn === 'favorites' &&
            !isCreateRow(s.favoriteCursor) &&
            s.gridMode === 'nav'
          ) {
            if (input === 'd') {
              s.gridMode = 'confirmDelete';
              refresh();
              return true;
            }
            if (input === 'r') {
              s.gridMode = 'rename';
              s.renameValue = s.favorites[s.favoriteCursor]?.name ?? '';
              refresh();
              return true;
            }
          }
          return false; // other letters are not actions — swallow
        case 'model':
        case 'effort':
        case 'worktree':
          if (input === 'j') {
            moveCursor(1);
            return true;
          }
          if (input === 'k') {
            moveCursor(-1);
            return true;
          }
          return false; // other letters are not actions on a list step — swallow
        case 'branch':
          s.branch = insertChar(s.branch, input);
          s.error = null;
          refresh();
          return true;
        case 'name':
          s.name = insertChar(s.name, input);
          refresh();
          return true;
        case 'nameFavorite':
          s.favoriteName = insertChar(s.favoriteName, input);
          s.error = null;
          refresh();
          return true;
        case 'context':
          // Item 7: y/n set the choice then advance() (so a following nameFavorite step runs when
          // creating a favorite; when not, context is last so advance() still submits).
          if (input === 'y') {
            s.contextAccepted = true;
            advance();
            return true;
          }
          if (input === 'n') {
            s.contextAccepted = false;
            advance();
            return true;
          }
          if (input === 'h') {
            if (!s.contextAccepted) {
              s.contextAccepted = true;
              refresh();
            }
            return true;
          }
          if (input === 'l') {
            if (s.contextAccepted) {
              s.contextAccepted = false;
              refresh();
            }
            return true;
          }
          return false;
        default:
          return false;
      }
    },
    render: () => (
      <SpawnWizardDialog state={s} hasContext={hasContext} spawnContext={spawnContext} />
    ),
  };

  return mode;
}

// ---------------------------------------------------------------------------------------------
// Presentation — pure functions of state (rule 1). No store/bus knowledge.
// ---------------------------------------------------------------------------------------------

function SpawnWizardDialog({
  state: s,
  hasContext,
  spawnContext,
}: {
  readonly state: SpawnWizardState;
  readonly hasContext: boolean;
  readonly spawnContext: SpawnContext | null;
}): JSX.Element {
  const theme = useTheme();
  // Design width 64, clamped to the live terminal so a narrow screen doesn't overflow the box.
  const width = useModalWidth(64);
  const progress = stepProgress(s.step, conditions(s, hasContext));

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.warning}
      paddingX={2}
      paddingY={1}
      width={width}
    >
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color={theme.warning}>
          Spawn Rogue
        </Text>
        <Text dimColor>
          {progress.index}/{progress.total}
        </Text>
      </Box>

      {s.step === 'harness' && <HarnessGrid state={s} />}

      {s.step === 'model' && (
        <SelectList
          header={`Select model (${s.harness.replace(/_/g, '-')}):`}
          items={modelsFor(s.harness, s.modelMap).map((m) => m.label)}
          cursor={s.modelCursor}
        />
      )}

      {s.step === 'effort' && (
        <SelectList
          header="Select effort level:"
          items={[...effortMatrixFor(s.harness, s.model).options]}
          cursor={s.effortCursor}
        />
      )}

      {s.step === 'worktree' && (
        <SelectList
          header="Select worktree:"
          items={s.worktreeOptions.map((o) => o.label)}
          cursor={s.worktreeCursor}
        />
      )}

      {s.step === 'branch' && (
        <TextStep
          label="New worktree branch name:"
          value={s.branch}
          placeholder="e.g. feature/my-work"
        />
      )}

      {s.step === 'name' && (
        <TextStep label="Rogue name:" value={s.name} placeholder="blank = autogenerate" />
      )}

      {s.step === 'nameFavorite' && (
        <TextStep
          label="Name this favorite config:"
          value={s.favoriteName}
          placeholder="e.g. OpusMed"
        />
      )}

      {s.step === 'context' && spawnContext !== null && (
        <ContextStep spawnContext={spawnContext} contextAccepted={s.contextAccepted} />
      )}

      {s.error !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>{s.error}</Text>
        </Box>
      )}
    </Box>
  );
}

/**
 * The first wizard step's two-column grid: enabled harnesses (left, bare-digit select) and saved
 * favorites + a `create favorite` row (right, command-modified-digit select). The active column's
 * cursor row is highlighted; the `confirmDelete` / `rename` sub-modes render a prompt below the grid.
 */
function HarnessGrid({ state: s }: { readonly state: SpawnWizardState }): JSX.Element {
  const theme = useTheme();

  const leftRows = s.enabledHarnesses.map((h) => h.replace(/_/g, '-'));
  const rightCount =
    s.favoritesStatus === 'loading' ? 1 : s.favorites.length < 10 ? s.favorites.length + 1 : 10;
  const rows = Math.max(leftRows.length, rightCount);

  const favoriteFocused = s.focusedColumn === 'favorites';
  const renamingName = s.gridMode === 'rename' ? (s.favorites[s.favoriteCursor]?.name ?? '') : null;
  const deletingName =
    s.gridMode === 'confirmDelete' ? (s.favorites[s.favoriteCursor]?.name ?? '') : null;

  const rightLabel = (i: number): string => {
    if (s.favoritesStatus === 'loading') return 'loading favorites…';
    const chord = `[${s.chordPrefix}-${i < 9 ? i + 1 : 0}]`;
    const name = i < s.favorites.length ? (s.favorites[i]?.name ?? '') : 'create favorite';
    return `${chord} ${name}`;
  };
  const leftColumnRows = Array.from({ length: rows }, (_, i) => {
    const name = leftRows[i] ?? null;
    return {
      key: name === null ? `left-empty-${i}` : `left-${name}`,
      label: name === null ? null : `[${i + 1}] ${name}`,
      highlit: !favoriteFocused && i === s.harnessCursor,
    };
  });
  const rightColumnRows = Array.from({ length: rows }, (_, i) => {
    const label = i >= rightCount ? null : rightLabel(i);
    return {
      key: label === null ? `right-empty-${i}` : `right-${label}`,
      label,
      highlit: s.favoritesStatus === 'ready' && favoriteFocused && i === s.favoriteCursor,
    };
  });

  return (
    <Box marginTop={1} flexDirection="column">
      <Box flexDirection="row" columnGap={4}>
        {/* Left column — enabled harnesses. */}
        <Box flexDirection="column">
          <Text>Select harness:</Text>
          <Box marginTop={1} flexDirection="column">
            {leftColumnRows.map((row) => {
              if (row.label === null) {
                return (
                  <Box key={row.key}>
                    <Text> </Text>
                  </Box>
                );
              }
              return (
                <Box key={row.key}>
                  {row.highlit ? (
                    <Text color={theme.warning} bold>
                      {'› '}
                      {row.label}
                    </Text>
                  ) : (
                    <Text dimColor>
                      {'  '}
                      {row.label}
                    </Text>
                  )}
                </Box>
              );
            })}
          </Box>
        </Box>
        {/* Right column — saved favorites + a create row. */}
        <Box flexDirection="column">
          <Text>Select favorite:</Text>
          <Box marginTop={1} flexDirection="column">
            {rightColumnRows.map((row) => {
              if (row.label === null) {
                return (
                  <Box key={row.key}>
                    <Text> </Text>
                  </Box>
                );
              }
              return (
                <Box key={row.key}>
                  {row.highlit ? (
                    <Text color={theme.warning} bold>
                      {'› '}
                      {row.label}
                    </Text>
                  ) : (
                    <Text dimColor>
                      {'  '}
                      {row.label}
                    </Text>
                  )}
                </Box>
              );
            })}
          </Box>
        </Box>
      </Box>

      {deletingName !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>
            Delete "{deletingName}"? press d to confirm, any other key to cancel
          </Text>
        </Box>
      )}
      {renamingName !== null && (
        <Box marginTop={1} flexDirection="column">
          <Text color={theme.warning}>Select favorite — rename:</Text>
          <Box marginTop={1}>
            <TextInput
              value={s.renameValue}
              placeholder="favorite name"
              focused
              color={theme.text}
            />
          </Box>
        </Box>
      )}
    </Box>
  );
}

/** A generic selection list — used by the harness/model/effort/worktree steps. */
function SelectList({
  header,
  items,
  cursor,
}: {
  readonly header: string;
  readonly items: readonly string[];
  readonly cursor: number;
}): JSX.Element {
  const theme = useTheme();
  return (
    <Box marginTop={1} flexDirection="column">
      <Text>{header}</Text>
      <Box marginTop={1} flexDirection="column">
        {items.map((item, i) => (
          <Box key={item}>
            {i === cursor ? (
              <Text color={theme.warning} bold>
                {'› '}
                {item}
              </Text>
            ) : (
              <Text dimColor>
                {'  '}
                {item}
              </Text>
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

/** A text-entry step — used by branch + name. */
function TextStep({
  label,
  value,
  placeholder,
}: {
  readonly label: string;
  readonly value: string;
  readonly placeholder: string;
}): JSX.Element {
  const theme = useTheme();
  return (
    <Box marginTop={1} flexDirection="column">
      <Text>{label}</Text>
      <Box marginTop={1}>
        <TextInput value={value} placeholder={placeholder} focused color={theme.text} />
      </Box>
    </Box>
  );
}

/** The reference-by-path include-doc step. */
function ContextStep({
  spawnContext,
  contextAccepted,
}: {
  readonly spawnContext: SpawnContext;
  readonly contextAccepted: boolean;
}): JSX.Element {
  const theme = useTheme();
  return (
    <Box marginTop={1} flexDirection="column">
      <Text>
        Include{' '}
        <Text color={theme.heading} bold>
          {spawnContext.title}
        </Text>{' '}
        as context?
      </Text>
      <Box marginTop={1} flexDirection="row" columnGap={2}>
        <Text color={contextAccepted ? theme.warning : theme.muted} bold={contextAccepted}>
          {contextAccepted ? '[yes]' : 'yes'}
        </Text>
        <Text color={!contextAccepted ? theme.warning : theme.muted} bold={!contextAccepted}>
          {!contextAccepted ? '[no]' : 'no'}
        </Text>
      </Box>
    </Box>
  );
}
