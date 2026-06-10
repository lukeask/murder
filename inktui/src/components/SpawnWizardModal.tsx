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
 *     `antigravity` / `pi` / `native_coding_crow` (no effort enum).
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
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import type { HarnessModel, HarnessModelsActions } from '../store/dialogs/harnessModelsActions.js';
import { modelsFor, STATIC_HARNESS_MODELS } from '../store/dialogs/harnessModelsActions.js';
import type { SpawnActions } from '../store/dialogs/spawnActions.js';
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
import { theme } from '../theme.js';
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
  error: string | null;
}

/** Build the {@link StepConditions} from current closure state — the input to the pure machine. */
function conditions(s: SpawnWizardState, hasContext: boolean): StepConditions {
  return {
    harness: s.harness,
    modelMap: s.modelMap,
    newWorktree: s.worktreeKey === NEW_WORKTREE_KEY,
    hasContext,
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

  // Mutable local state. Start on the harness step with claude_code preselected.
  const s: SpawnWizardState = {
    step: 'harness',
    modelMap: STATIC_HARNESS_MODELS,
    worktreeOptions: buildWorktreeOptions([]),
    harness: DEFAULT_HARNESS,
    model: '',
    effort: '',
    worktreeKey: null,
    branch: '',
    name: '',
    contextAccepted: true,
    harnessCursor: 0,
    modelCursor: 0,
    effortCursor: defaultEffortCursor(DEFAULT_HARNESS),
    worktreeCursor: 0,
    error: null,
  };

  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
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

  /** The number of selectable rows on the active selection step (for cursor wrapping). */
  function currentListLength(): number {
    switch (s.step) {
      case 'harness':
        return HARNESS_ORDER.length;
      case 'model':
        return modelsFor(s.harness, s.modelMap).length;
      case 'effort':
        return effortMatrixFor(s.harness).options.length;
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
    s.harness = HARNESS_ORDER[s.harnessCursor] ?? DEFAULT_HARNESS;
    s.model = '';
    s.modelCursor = 0;
    s.effort = '';
    s.effortCursor = defaultEffortCursor(s.harness);
  }

  /** Capture the current step's selection, then advance to the next active step (or submit). */
  function advance(): void {
    switch (s.step) {
      case 'harness':
        onHarnessChanged();
        break;
      case 'model':
        s.model = modelsFor(s.harness, s.modelMap)[s.modelCursor]?.id ?? '';
        break;
      case 'effort':
        s.effort = effortMatrixFor(s.harness).options[s.effortCursor] ?? '';
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
    const effort = effortMatrixFor(s.harness).options.length > 0 ? s.effort : '';
    // The model is the picker selection, or `''` for harnesses that skip the model step (cursor /
    // antigravity / pi / native_coding_crow). The live handler requires a STRING but tolerates the
    // empty one (`model.strip() or None` → the adapter picks its own default), so we must NOT force
    // a Claude id like 'sonnet' onto a non-Claude harness — that is the same invalid-id bug class
    // this rewrite fixes for the harness field. See orchestrator.py:573 (isinstance str) + :504.
    const model = s.model;
    const kickoffMessage = buildKickoffMessage();
    const wt = resolveWorktreePayload(s.worktreeKey, s.branch);
    const name = s.name.trim();
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
        toastStore.getState().push(message, { severity: 'error', ttlMs: 6000 });
      });
  }

  const mode: Mode<SpawnWizardIntent> = {
    id,
    presentation: 'modal',
    // Structural keys only — printable chars (j/k/y/n + free text) ride `onUncaptured`.
    keymap: [
      { chord: { key: { downArrow: true } }, intent: 'cursorDown', description: 'next option' },
      { chord: { key: { upArrow: true } }, intent: 'cursorUp', description: 'prev option' },
      { chord: { key: { return: true } }, intent: 'confirm', description: 'confirm' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { input: 'u', key: { meta: true } }, intent: 'deleteAll', description: 'clear' },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      const isList =
        s.step === 'harness' || s.step === 'model' || s.step === 'effort' || s.step === 'worktree';
      const isText = s.step === 'branch' || s.step === 'name';
      switch (intent) {
        case 'cursorUp':
          if (isList) moveCursor(-1);
          break;
        case 'cursorDown':
          if (isList) moveCursor(1);
          break;
        case 'confirm':
          advance();
          break;
        case 'backspace':
          if (isText) {
            if (s.step === 'branch') s.branch = deleteLastChar(s.branch);
            else s.name = deleteLastChar(s.name);
            refresh();
          }
          break;
        case 'deleteAll':
          if (isText) {
            if (s.step === 'branch') s.branch = '';
            else s.name = '';
            refresh();
          }
          break;
        case 'dismiss':
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
      if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
        return false;
      }
      switch (s.step) {
        case 'harness':
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
        case 'context':
          if (input === 'y') {
            s.contextAccepted = true;
            doSubmit();
            return true;
          }
          if (input === 'n') {
            s.contextAccepted = false;
            doSubmit();
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
  const progress = stepProgress(s.step, conditions(s, hasContext));

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.warning}
      paddingX={2}
      paddingY={1}
      width={64}
    >
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color={theme.warning}>
          Spawn Rogue
        </Text>
        <Text dimColor>
          {progress.index}/{progress.total}
        </Text>
      </Box>

      {s.step === 'harness' && (
        <SelectList
          header="Select harness:"
          items={HARNESS_ORDER.map((h) => h.replace(/_/g, '-'))}
          cursor={s.harnessCursor}
        />
      )}

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
          items={[...effortMatrixFor(s.harness).options]}
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
      <Box marginTop={1}>
        <Text dimColor>j/k: navigate · enter: confirm · esc: cancel</Text>
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
  return (
    <Box marginTop={1} flexDirection="column">
      <Text>{label}</Text>
      <Box marginTop={1}>
        <TextInput value={value} placeholder={placeholder} focused color={theme.text} />
      </Box>
      <Box marginTop={1}>
        <Text dimColor>enter: confirm · esc: cancel</Text>
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
      <Box marginTop={1}>
        <Text dimColor>y/enter: include · n: skip · esc: cancel</Text>
      </Box>
    </Box>
  );
}
