/**
 * `SpawnWizardModal` — the `ctrl+s` spawn wizard: a **multi-step modal C7M mode** that collects
 * an effort level (step 1) and an optional spawn-context reference (step 2, conditionally shown),
 * then fires `crow.spawn_rogue` with the collected params.
 *
 * ## Steps
 *
 *  1. **Effort selection** — a list of per-harness effort options (passed as `effortOptions` to the
 *     factory; defaults to `['low', 'medium', 'high']`). The user navigates with `j`/`k` or arrow
 *     keys and confirms with Enter.
 *  2. **Spawn context** (conditional — shown only when `spawnContext` is non-null) — asks:
 *     "Include `{title}` as context? [yes]/no". Default is **yes**. If yes, the kickoff message
 *     tells the rogue to *read* `.murder/<dir>/<name>.md` (**reference-by-path**, locked mechanism).
 *     The user presses `y`/Enter (accept) or `n` (decline); either moves to submit.
 *
 * ## Spawn context — reference-by-path (locked mechanism)
 *
 * When the user accepts the context doc, the wizard constructs:
 *   `"Please read ${spawnContext.path} before starting."`
 * This tells the rogue to *read* the document, not inline its body. The read tool-use lands the doc
 * as something the rogue actively did — priming engagement (same rationale as ticket crows reading
 * their own ticket file). The `path` is the relative `.murder/<dir>/<name>.md` path constructed
 * by the caller (the `spawn` handler in `useRootInput`/`App.tsx`).
 *
 * ## C13 seam — cursor-in-store (C11)
 *
 * The `spawnContext` parameter is computed by the caller at `ctrl+s` invocation time from the
 * focused panel + app store. Because cursor state is local `useState` in each panel component,
 * the caller currently uses the first available row as a best-effort proxy for the "selected" doc.
 * When C11 lands doc-toggle with full doc-focus tracking (cursor exposed as store state), the
 * `spawn` handler in `App.tsx`/`useRootInput` should be updated to read the real cursor position.
 * The wizard factory interface is intentionally seam-ready: `spawnContext: SpawnContext | null`
 * with a structured `{ title, path }` — the wizard itself has no coupling to the seam location.
 *
 * ## Recipe (C12 pattern — copied from `NewPlanModal`)
 *
 *  1. **Intent union** for every special key action. Printable input from the search/filter is
 *     not needed here (effort uses selection, not typing); the keymap covers all interactions.
 *  2. **Mode factory** with mutable closure state + `refresh()` for re-render (C12 pattern).
 *  3. **Enter from the global chord handler:**
 *     `modes.getState().enter(spawnWizardMode(modes, actions, { spawnContext, effortOptions }))`.
 *  4. {@link ../components/Overlay.js Overlay} centers it; C7M handles capture + focus restore.
 *
 * ## `passThrough: false` (default)
 *
 * The wizard captures every key while up — global chords cannot fire underneath it.
 * `onUncaptured` is not needed here (no free-text fields; all input is selection-based).
 *
 * **Bus status:** `crow.spawn_rogue` is on the bus per service B10. See {@link ../store/dialogs/spawnActions.js}.
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import type { SpawnActions } from '../store/dialogs/spawnActions.js';

/** Intent union for the spawn wizard — all key actions. No printable-char capture needed since
 * effort is a selection list and the context step is a yes/no. */
type SpawnWizardIntent =
  | 'cursorUp'
  | 'cursorDown'
  | 'confirm'
  | 'contextYes'
  | 'contextNo'
  | 'dismiss';

/** A document that can be included as spawn context (reference-by-path). */
export interface SpawnContext {
  /** The display title of the doc (e.g. `'my-plan'`). */
  readonly title: string;
  /**
   * The `.murder/`-relative path to read (e.g. `.murder/plans/my-plan.md`).
   * The wizard constructs the kickoff message as:
   *   `"Please read ${path} before starting."`
   */
  readonly path: string;
}

/** Options passed to the spawn wizard mode factory. */
export interface SpawnWizardModeOptions {
  /**
   * Per-harness effort options. Passed from the `spawn` handler so they can be customised per
   * session/harness. Defaults to `DEFAULT_EFFORT_OPTIONS` if absent.
   */
  readonly effortOptions?: readonly string[];
  /**
   * The focused doc, if one was detected at `ctrl+s` invocation time. When non-null, step 2
   * (context) is shown; when null, the wizard goes straight from effort to submit.
   *
   * ## C11 seam
   * Computed by the `spawn` handler in `App.tsx` from the focused panel + app store. Currently
   * a best-effort proxy (first available row) because cursor is local `useState`. When C11 lands
   * doc-toggle with cursor-in-store, update the `spawn` handler to read the real cursor. The
   * wizard interface is seam-ready — no changes here when C11 lands.
   */
  readonly spawnContext?: SpawnContext | null;
  /** Called with the RPC result after a successful spawn (fired after mode exits). */
  readonly onSubmit?: (effort: string, kickoffMessage: string | null) => void;
  /** Called when dismissed without spawning. */
  readonly onDismiss?: () => void;
}

/** The default effort options — used when `effortOptions` is not supplied. Per the plan: "effort is
 * a per-harness enum" — these are reasonable defaults; the caller should supply harness-specific
 * options when available. */
export const DEFAULT_EFFORT_OPTIONS: readonly string[] = ['low', 'medium', 'high'];

/** The stable mode id for idempotent re-enter. */
export const SPAWN_WIZARD_MODE_ID = 'spawn-wizard';

/** The two wizard steps. Step 2 is skipped when no spawn context is available. */
type WizardStep = 'effort' | 'context';

/** Mutable closure state — not React state. Mutated by `onIntent`; `render` reads at call time. */
interface SpawnWizardState {
  step: WizardStep;
  effortCursor: number;
  /** Whether the user accepted the context doc (default true, shown only when context is non-null). */
  contextAccepted: boolean;
  error: string | null;
}

/**
 * Build the spawn wizard {@link Mode}. Enter via:
 * `modes.getState().enter(spawnWizardMode(modes, actions, opts))`.
 *
 * The global `spawn` handler in `useRootInput` (wired in `App.tsx`'s `Shell`) calls this when
 * `ctrl+s` fires.
 */
export function spawnWizardMode(
  modes: ModeStoreApi,
  actions: SpawnActions,
  opts: SpawnWizardModeOptions = {},
): Mode<SpawnWizardIntent> {
  const id = SPAWN_WIZARD_MODE_ID;
  const effortOptions = opts.effortOptions ?? DEFAULT_EFFORT_OPTIONS;
  const spawnContext = opts.spawnContext ?? null;

  // Mutable local state in the closure — not React state.
  const s: SpawnWizardState = {
    step: 'effort',
    effortCursor: 0,
    contextAccepted: true,
    error: null,
  };

  // Re-render by poking the mode store (C12 pattern: re-enter same id → new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  /** Build the kickoff message for reference-by-path when context is accepted. */
  function buildKickoffMessage(): string | null {
    if (spawnContext === null || !s.contextAccepted) {
      return null;
    }
    return `Please read ${spawnContext.path} before starting.`;
  }

  /** Submit: exit-then-act (exits first to restore focus, then fires the async RPC). */
  function doSubmit(): void {
    modes.getState().exit(id);
    const effort = effortOptions[s.effortCursor] ?? effortOptions[0] ?? 'medium';
    const kickoffMessage = buildKickoffMessage();
    const params =
      kickoffMessage !== null ? { effort, kickoff_message: kickoffMessage } : { effort };
    void actions
      .spawnRogue(params)
      .then(() => {
        opts.onSubmit?.(effort, kickoffMessage);
      })
      .catch(() => {
        // Bus error: focus is already restored. Silent drop — surface via toast when bus is live.
      });
  }

  const mode: Mode<SpawnWizardIntent> = {
    id,
    presentation: 'modal',
    // No pass-through: the wizard captures every key while up.
    keymap: [
      // Effort step: j/down and k/up navigate the effort list.
      { chord: { input: 'j' }, intent: 'cursorDown', description: 'next option' },
      { chord: { key: { downArrow: true } }, intent: 'cursorDown', description: 'next option' },
      { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev option' },
      { chord: { key: { upArrow: true } }, intent: 'cursorUp', description: 'prev option' },
      // Enter: confirm the current step.
      { chord: { key: { return: true } }, intent: 'confirm', description: 'confirm' },
      // Context step: y = accept, n = decline.
      { chord: { input: 'y' }, intent: 'contextYes', description: 'include context' },
      { chord: { input: 'n' }, intent: 'contextNo', description: 'skip context' },
      // Escape: dismiss.
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'cursorUp': {
          if (s.step === 'effort') {
            s.effortCursor = (s.effortCursor - 1 + effortOptions.length) % effortOptions.length;
            refresh();
          }
          break;
        }
        case 'cursorDown': {
          if (s.step === 'effort') {
            s.effortCursor = (s.effortCursor + 1) % effortOptions.length;
            refresh();
          }
          break;
        }
        case 'confirm': {
          if (s.step === 'effort') {
            if (spawnContext !== null) {
              // Advance to the context step.
              s.step = 'context';
              refresh();
            } else {
              // No context — submit directly.
              doSubmit();
            }
          } else if (s.step === 'context') {
            // Enter on context step = confirm the current selection (default: yes).
            doSubmit();
          }
          break;
        }
        case 'contextYes': {
          if (s.step === 'context') {
            s.contextAccepted = true;
            doSubmit();
          }
          break;
        }
        case 'contextNo': {
          if (s.step === 'context') {
            s.contextAccepted = false;
            doSubmit();
          }
          break;
        }
        case 'dismiss': {
          modes.getState().exit(id);
          opts.onDismiss?.();
          break;
        }
        default:
          return intent satisfies never;
      }
    },
    render: () => (
      <SpawnWizardDialog
        step={s.step}
        effortOptions={effortOptions}
        effortCursor={s.effortCursor}
        spawnContext={spawnContext}
        contextAccepted={s.contextAccepted}
        error={s.error}
      />
    ),
  };

  return mode;
}

/** The wizard's visual presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function SpawnWizardDialog({
  step,
  effortOptions,
  effortCursor,
  spawnContext,
  contextAccepted,
  error,
}: {
  readonly step: WizardStep;
  readonly effortOptions: readonly string[];
  readonly effortCursor: number;
  readonly spawnContext: SpawnContext | null;
  readonly contextAccepted: boolean;
  readonly error: string | null;
}): JSX.Element {
  const totalSteps = spawnContext !== null ? 2 : 1;
  const stepIndex = step === 'effort' ? 1 : 2;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
      width={60}
    >
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color="yellow">
          Spawn Rogue
        </Text>
        <Text dimColor>
          {stepIndex}/{totalSteps}
        </Text>
      </Box>

      {step === 'effort' && (
        <EffortStep effortOptions={effortOptions} effortCursor={effortCursor} />
      )}

      {step === 'context' && spawnContext !== null && (
        <ContextStep spawnContext={spawnContext} contextAccepted={contextAccepted} />
      )}

      {error !== null && (
        <Box marginTop={1}>
          <Text color="red">{error}</Text>
        </Box>
      )}
    </Box>
  );
}

/** Step 1 — effort selection. */
function EffortStep({
  effortOptions,
  effortCursor,
}: {
  readonly effortOptions: readonly string[];
  readonly effortCursor: number;
}): JSX.Element {
  return (
    <Box marginTop={1} flexDirection="column">
      <Text>Select effort level:</Text>
      <Box marginTop={1} flexDirection="column">
        {effortOptions.map((option, i) => (
          <Box key={option}>
            {i === effortCursor ? (
              <Text color="yellow" bold>
                {'› '}
                {option}
              </Text>
            ) : (
              <Text dimColor>
                {'  '}
                {option}
              </Text>
            )}
          </Box>
        ))}
      </Box>
      <Box marginTop={1}>
        <Text dimColor>j/k: navigate enter: confirm esc: cancel</Text>
      </Box>
    </Box>
  );
}

/** Step 2 — context include/skip. */
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
        <Text color="cyan" bold>
          {spawnContext.title}
        </Text>{' '}
        as context?
      </Text>
      <Box marginTop={1} flexDirection="row" columnGap={2}>
        <Text color={contextAccepted ? 'yellow' : 'gray'} bold={contextAccepted}>
          {contextAccepted ? '[yes]' : 'yes'}
        </Text>
        <Text color={!contextAccepted ? 'yellow' : 'gray'} bold={!contextAccepted}>
          {!contextAccepted ? '[no]' : 'no'}
        </Text>
      </Box>
      <Box marginTop={1}>
        <Text dimColor>y/enter: include n: skip esc: cancel</Text>
      </Box>
    </Box>
  );
}
