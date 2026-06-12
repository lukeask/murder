/**
 * `SettingsModal` — the `alt+o` / `ctrl+o` (`global.settings`) settings menu: a **modal C7M mode** (the
 * {@link ./SpawnWizardModal.js} mode-factory idiom — `settingsMode(...)`, `presentation: 'modal'`,
 * rendered through the {@link ./Overlay.js Overlay}). Three sections, navigated as one flat cursor
 * list of rows (j/k or arrows move the cursor; Enter acts on the focused row):
 *
 *  1. **Modifier** — a radio over `alt` / `ctrl` / `both`. The `ctrl` and `both` rows are *disabled*
 *     (un-selectable, dimmed) with an inline notice when the terminal cannot deliver ctrl chords
 *     ({@link ../terminal/capsStore.js kittySupported} === `false`). Selecting a row commits the
 *     modifier immediately (live: the dispatcher/footer/shim react at once).
 *  2. **Theme** — a select over the known {@link ../theme/palettes.js ThemeId}s. Moving the cursor
 *     onto a theme row **live-previews** it ({@link ../theme/themeStore.js setTheme} fires on cursor
 *     move, recoloring the whole UI under the modal); the preview is *committed* only on Save and is
 *     *reverted* to the persisted value on cancel/Esc — so browsing themes never persists a half-pick.
 *  3. **Key bindings** — the rebindable actions from {@link ../input/bindings.js ACTIONS} with their
 *     resolved labels. Enter on a binding row enters *capture-next-key* mode: the very next key
 *     (caught by `onUncaptured`) becomes the new chord's key char. Rejected: `ctrl+c/d/z` (reserved),
 *     digits (panel toggles), and any char already bound to another action (collision).
 *
 * Every commit routes through `actions.settings.update(partial)` (optimistic; the stores react, so
 * the dispatcher/keymaps/footer/shim update live — see {@link ../store/settings/settingsActions.js}).
 * The modal holds only *draft* selections in closure state; `update` is the single persistence path.
 *
 * ## Input model (keymap vs. onUncaptured)
 *
 * Like the spawn wizard: the keymap carries ONLY structural keys (arrows, return, escape); the
 * printable router lives in `onUncaptured`. In the normal (non-capturing) state `onUncaptured` maps
 * `j`/`k` to cursor moves. In capture mode `onUncaptured` instead consumes the next printable char as
 * the rebind target (with the rejection rules above).
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useModalWidth } from '../hooks/useTerminalSize.js';
import {
  ACTION_IDS,
  ACTIONS,
  type ActionId,
  type Modifier,
  resolveBindings,
} from '../input/bindings.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import type { SettingsActions } from '../store/settings/settingsActions.js';
import { capsStore, type KittySupport, useKittySupport } from '../terminal/capsStore.js';
import { PALETTES, type ThemeId } from '../theme/palettes.js';
import { setTheme, useTheme } from '../theme/themeStore.js';

// Bring the dispatcher's `onUncaptured` augmentation into scope (the printable/capture router needs it).
import '../input/dispatcher.js';

/** Intent union — structural-key actions only. Printable chars (j/k + the captured rebind key) ride
 * `onUncaptured`, not the keymap. */
type SettingsIntent = 'cursorUp' | 'cursorDown' | 'confirm' | 'cancel';

/** The modifier choices in display order. */
const MODIFIERS: readonly Modifier[] = ['alt', 'ctrl', 'both'];

/** The pane-gap choices in display order — spaces between adjacent pane borders. `0` = flush
 * borders (the default); `1`–`4` add spacing. Mirrors the Python `TuiUserConfig.pane_gap` range
 * (`ge=0, le=4`). */
const GAP_OPTIONS: readonly number[] = [0, 1, 2, 3, 4];

/** The rebindable actions, in registry order — the rows the bindings section shows. */
const REBINDABLE: readonly ActionId[] = ACTION_IDS.filter((id) => ACTIONS[id].rebindable);

/** Key chars that may NEVER be a rebind target. `c`/`d`/`z` collide with the always-literal ctrl
 * exits (ctrl+c/d/z), and digits are reserved for panel toggles (never rebindable/interceptable —
 * see the plan's risks). A captured char in this set is rejected with an inline notice. */
const RESERVED_KEYS: ReadonlySet<string> = new Set([
  'c',
  'd',
  'z',
  '0',
  '1',
  '2',
  '3',
  '4',
  '5',
  '6',
  '7',
  '8',
  '9',
]);

/** Options for the settings mode factory. */
export interface SettingsModeOptions {
  /** Called when the modal is dismissed (after the mode exits). */
  readonly onDismiss?: () => void;
}

/** The stable mode id for idempotent re-enter. */
export const SETTINGS_MODE_ID = 'settings';

/** A flat row in the modal — the cursor walks these across all three sections. A `section` header row
 * is non-focusable (the cursor skips it); a `modifier`/`theme`/`binding` row is selectable. */
type Row =
  | { readonly kind: 'header'; readonly label: string }
  | { readonly kind: 'modifier'; readonly value: Modifier }
  | { readonly kind: 'theme'; readonly value: ThemeId }
  | { readonly kind: 'gap'; readonly value: number }
  | { readonly kind: 'binding'; readonly action: ActionId };

/** Build the flat row list (headers + selectable rows) in section order. Pure — depends only on the
 * static registries, so it is stable across renders. */
function buildRows(): readonly Row[] {
  const rows: Row[] = [{ kind: 'header', label: 'Command modifier' }];
  for (const value of MODIFIERS) {
    rows.push({ kind: 'modifier', value });
  }
  rows.push({ kind: 'header', label: 'Theme' });
  for (const value of Object.keys(PALETTES) as ThemeId[]) {
    rows.push({ kind: 'theme', value });
  }
  rows.push({ kind: 'header', label: 'Pane gap' });
  for (const value of GAP_OPTIONS) {
    rows.push({ kind: 'gap', value });
  }
  rows.push({ kind: 'header', label: 'Key bindings' });
  for (const action of REBINDABLE) {
    rows.push({ kind: 'binding', action });
  }
  return rows;
}

const ROWS = buildRows();

/** Whether a row can hold the cursor (headers are skipped). A modifier row is selectable even when
 * disabled — the cursor can rest on it (to show the notice), but `confirm` is a no-op there. */
function isSelectable(row: Row): boolean {
  return row.kind !== 'header';
}

/** Mutable closure state — not React state. Mutated by `onIntent`/`onUncaptured`; `render` reads it. */
interface SettingsState {
  /** The cursor's row index (always on a selectable row). */
  cursor: number;
  /** The draft modifier (committed via `update` on selection). */
  modifier: Modifier;
  /** The persisted theme at open time — what a cancel reverts the live preview back to. */
  persistedTheme: ThemeId;
  /** The draft theme (the live-previewed selection; committed on Save). */
  theme: ThemeId;
  /** The draft pane-gap (committed via `update` on selection — the layout reacts at once). */
  paneGap: number;
  /** The draft per-action key overrides (`ActionId -> key char`). */
  overrides: Record<string, string>;
  /** The action currently capturing its next-key rebind, or `null` when not capturing. */
  capturing: ActionId | null;
  /** The transient inline notice (rejection reason / hint), or `null`. */
  notice: string | null;
}

/** Find the first selectable row index at or after `from` (wrapping). Used to seed the cursor and to
 * skip header rows during navigation. */
function firstSelectableFrom(from: number): number {
  for (let i = 0; i < ROWS.length; i++) {
    const idx = (from + i) % ROWS.length;
    const row = ROWS[idx];
    if (row !== undefined && isSelectable(row)) {
      return idx;
    }
  }
  return 0;
}

/** The resolved label for a binding row's current chord, honouring the draft overrides + modifier (so
 * the row reflects an in-session rebind/modifier change before it is even saved). `ctrlAvailable` is
 * irrelevant to the displayed *key char*, so it is left false here. */
function bindingLabel(
  action: ActionId,
  modifier: Modifier,
  overrides: Record<string, string>,
): string {
  return resolveBindings(modifier, false, overrides as Partial<Record<ActionId, string>>).label(
    action,
  );
}

/**
 * Build the settings {@link Mode}. Enter via
 * `modes.getState().enter(settingsMode(modes, actions, settings, opts))`, where `settings` is the
 * current persisted slice value (so the modal opens reflecting the live preferences).
 */
export function settingsMode(
  modes: ModeStoreApi,
  actions: SettingsActions,
  current: {
    readonly modifier: Modifier;
    readonly theme: ThemeId;
    readonly paneGap: number;
    readonly keyOverrides: Record<string, string>;
  },
  opts: SettingsModeOptions = {},
): Mode<SettingsIntent> {
  const id = SETTINGS_MODE_ID;

  const s: SettingsState = {
    cursor: firstSelectableFrom(0),
    modifier: current.modifier,
    persistedTheme: current.theme,
    theme: current.theme,
    paneGap: current.paneGap,
    overrides: { ...current.keyOverrides },
    capturing: null,
    notice: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((f) => f.mode.id === id);
    if (frame !== undefined) {
      modes.getState().enter(frame.mode);
    }
  }

  /** Move the cursor by `delta`, skipping header rows (wrapping). */
  function moveCursor(delta: number): void {
    const len = ROWS.length;
    let idx = s.cursor;
    for (let step = 0; step < len; step++) {
      idx = (idx + delta + len) % len;
      const row = ROWS[idx];
      if (row !== undefined && isSelectable(row)) {
        s.cursor = idx;
        // Live theme preview: landing the cursor on a theme row applies it immediately (committed on
        // Save, reverted on cancel — see the class doc). Leaving a theme row keeps the preview until
        // the cursor lands on a *different* theme (or the modal is dismissed).
        if (row.kind === 'theme') {
          s.theme = row.value;
          setTheme(row.value);
        }
        s.notice = null;
        refresh();
        return;
      }
    }
  }

  /** Is ctrl deliverable right now? Read live so a detection that resolves while the modal is open
   * un-disables the ctrl/both rows. */
  function ctrlAvailable(): boolean {
    return kittySupported() === true;
  }

  /** Commit the draft modifier (optimistic update). Disabled rows are a no-op (with a notice). */
  function selectModifier(value: Modifier): void {
    if ((value === 'ctrl' || value === 'both') && !ctrlAvailable()) {
      s.notice = CTRL_UNSUPPORTED_NOTICE;
      refresh();
      return;
    }
    s.modifier = value;
    void actions.update({ modifier: value });
    s.notice = null;
    refresh();
  }

  /** Commit the draft pane gap (optimistic update). The Body/Stage/Rail react at once via the slice. */
  function selectGap(value: number): void {
    s.paneGap = value;
    void actions.update({ pane_gap: value });
    s.notice = null;
    refresh();
  }

  /** Begin capturing the next key for a binding row's rebind. */
  function beginCapture(action: ActionId): void {
    s.capturing = action;
    s.notice = `Press a key to bind "${ACTIONS[action].description}" (Esc to cancel)`;
    refresh();
  }

  /** Apply a captured rebind char (after the rejection rules) and persist it. */
  function applyCapture(action: ActionId, char: string): void {
    const lower = char.toLowerCase();
    if (RESERVED_KEYS.has(lower)) {
      s.notice = `"${char}" is reserved and cannot be rebound`;
      s.capturing = null;
      refresh();
      return;
    }
    // Collision: the char is already bound (by default or override) to a DIFFERENT action.
    const collision = REBINDABLE.find((other) => {
      if (other === action) {
        return false;
      }
      const otherKey = s.overrides[other] ?? actionDefaultKey(other);
      return otherKey === lower;
    });
    if (collision !== undefined) {
      s.notice = `"${char}" is already bound to "${ACTIONS[collision].description}"`;
      s.capturing = null;
      refresh();
      return;
    }
    s.overrides = { ...s.overrides, [action]: lower };
    s.capturing = null;
    s.notice = null;
    void actions.update({ key_overrides: s.overrides });
    refresh();
  }

  /** Act on the focused row (Enter). Modifier → commit; theme → already previewed, just confirm Save;
   * binding → begin capture. */
  function confirm(): void {
    const row = ROWS[s.cursor];
    if (row === undefined) {
      return;
    }
    switch (row.kind) {
      case 'modifier':
        selectModifier(row.value);
        break;
      case 'theme':
        // The preview already applied on cursor-move; Enter commits it to the persisted config.
        s.persistedTheme = row.value;
        void actions.update({ theme: row.value });
        s.notice = 'Theme saved';
        refresh();
        break;
      case 'gap':
        selectGap(row.value);
        break;
      case 'binding':
        beginCapture(row.action);
        break;
      default:
        break;
    }
  }

  /** Dismiss the modal, reverting any uncommitted live theme preview back to the persisted value. */
  function dismiss(): void {
    if (s.capturing !== null) {
      // Esc during capture cancels the capture only (stay in the modal).
      s.capturing = null;
      s.notice = null;
      refresh();
      return;
    }
    // Revert the live preview to the last persisted theme (a browsed-but-unsaved theme is discarded).
    if (s.theme !== s.persistedTheme) {
      setTheme(s.persistedTheme);
    }
    modes.getState().exit(id);
    opts.onDismiss?.();
  }

  const mode: Mode<SettingsIntent> = {
    id,
    presentation: 'modal',
    keymap: [
      { chord: { key: { downArrow: true } }, intent: 'cursorDown', description: 'next' },
      { chord: { key: { upArrow: true } }, intent: 'cursorUp', description: 'prev' },
      { chord: { key: { return: true } }, intent: 'confirm', description: 'select' },
      { chord: { key: { escape: true } }, intent: 'cancel', description: 'close' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'cursorUp':
          if (s.capturing === null) {
            moveCursor(-1);
          }
          break;
        case 'cursorDown':
          if (s.capturing === null) {
            moveCursor(1);
          }
          break;
        case 'confirm':
          if (s.capturing === null) {
            confirm();
          }
          break;
        case 'cancel':
          dismiss();
          break;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      // Capture mode: the next printable char (no ctrl/meta — those aren't a base key char) becomes
      // the rebind target. ctrl+<x> is rejected up-front (a captured rebind is always a bare key).
      if (s.capturing !== null) {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
          return false; // not a bare printable — let the keymap (Esc/return) or swallow handle it
        }
        applyCapture(s.capturing, input);
        return true;
      }
      // Normal mode: j/k navigate (mirrors the spawn wizard's list steps).
      if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
        return false;
      }
      if (input === 'j') {
        moveCursor(1);
        return true;
      }
      if (input === 'k') {
        moveCursor(-1);
        return true;
      }
      return false; // other chars are not actions here — swallow under the modal
    },
    render: () => <SettingsDialog state={s} />,
  };

  return mode;
}

/** A snapshot read of the global caps store, for the mode factory (a non-React call site). The render
 * side uses {@link useKittySupport} so the notice reacts; the factory uses this for its commit guard
 * (the modifier-select gate). */
function kittySupported(): KittySupport {
  return capsStore.getState().kittySupported;
}

/** The default key char for a rebindable action (a `command`-kind default; rebindable actions are
 * all `command`). */
function actionDefaultKey(action: ActionId): string {
  const def = ACTIONS[action].default;
  return def.kind === 'command' ? def.key : '';
}

/** The shared ctrl-unsupported notice (also shown inline under the modifier section). */
const CTRL_UNSUPPORTED_NOTICE =
  'ctrl requires the kitty keyboard protocol — not supported by this terminal; ' +
  'inside tmux needs ≥3.3 with extended-keys on';

// ---------------------------------------------------------------------------------------------
// Presentation — pure functions of state (rule 1). Reads the live caps/theme stores via hooks.
// ---------------------------------------------------------------------------------------------

function SettingsDialog({ state: s }: { readonly state: SettingsState }): JSX.Element {
  const theme = useTheme();
  // Design width 64, clamped to the live terminal so a narrow screen doesn't overflow the box.
  const width = useModalWidth(64);
  const kitty = useKittySupport();
  const ctrlAvailable = kitty === true;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={width}
    >
      <Text bold color={theme.heading}>
        Settings
      </Text>

      <Box marginTop={1} flexDirection="column">
        {ROWS.map((row, i) => (
          <RowView
            key={rowKey(row)}
            row={row}
            focused={i === s.cursor}
            state={s}
            theme={theme}
            ctrlAvailable={ctrlAvailable}
          />
        ))}
      </Box>

      {/* The ctrl-unsupported notice is always shown under the modifier section when ctrl can't be
          delivered, so the user understands why the ctrl/both rows are disabled. */}
      {!ctrlAvailable && (
        <Box marginTop={1}>
          <Text color={theme.muted}>{CTRL_UNSUPPORTED_NOTICE}</Text>
        </Box>
      )}

      {s.notice !== null && (
        <Box marginTop={1}>
          <Text color={theme.warning}>{s.notice}</Text>
        </Box>
      )}

      <Box marginTop={1}>
        <Text dimColor>j/k: navigate · enter: select/rebind · esc: close</Text>
      </Box>
    </Box>
  );
}

/** A stable React key for a row. */
function rowKey(row: Row): string {
  switch (row.kind) {
    case 'header':
      return `header:${row.label}`;
    case 'modifier':
      return `modifier:${row.value}`;
    case 'theme':
      return `theme:${row.value}`;
    case 'gap':
      return `gap:${row.value}`;
    case 'binding':
      return `binding:${row.action}`;
  }
}

/** Render one flat row by kind. */
function RowView({
  row,
  focused,
  state: s,
  theme,
  ctrlAvailable,
}: {
  readonly row: Row;
  readonly focused: boolean;
  readonly state: SettingsState;
  readonly theme: ReturnType<typeof useTheme>;
  readonly ctrlAvailable: boolean;
}): JSX.Element {
  if (row.kind === 'header') {
    return (
      <Box marginTop={1}>
        <Text bold color={theme.accent}>
          {row.label}
        </Text>
      </Box>
    );
  }

  const cursor = focused ? '› ' : '  ';

  if (row.kind === 'modifier') {
    const selected = row.value === s.modifier;
    const disabled = (row.value === 'ctrl' || row.value === 'both') && !ctrlAvailable;
    const mark = selected ? '(•) ' : '( ) ';
    const color = disabled ? theme.muted : focused ? theme.warning : theme.text;
    return (
      <Box>
        <Text color={color} bold={focused} dimColor={disabled}>
          {cursor}
          {mark}
          {row.value}
          {disabled ? ' (unavailable)' : ''}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'theme') {
    const selected = row.value === s.theme;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    return (
      <Box>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {row.value}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'gap') {
    const selected = row.value === s.paneGap;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    // A live border preview: `│` + N spaces + `│` shows exactly the gap N produces between panes.
    const preview = `│${' '.repeat(row.value)}│`;
    return (
      <Box>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {row.value}
          {row.value === 0 ? ' (flush)' : ''}
        </Text>
        <Text color={theme.muted}>
          {'  '}
          {preview}
        </Text>
      </Box>
    );
  }

  // binding row
  const capturing = s.capturing === row.action;
  const label = bindingLabel(row.action, s.modifier, s.overrides);
  const color = focused ? theme.warning : theme.text;
  return (
    <Box>
      <Text color={color} bold={focused}>
        {cursor}
        {ACTIONS[row.action].description}
      </Text>
      <Text color={capturing ? theme.heading : theme.muted}>
        {'  '}
        {capturing ? '[press a key…]' : label}
      </Text>
    </Box>
  );
}
