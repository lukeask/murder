/**
 * `SettingsModal` — the `alt+o` / `ctrl+o` (`global.settings`) settings menu: a **modal C7M mode** (the
 * {@link ./SpawnWizardModal.js} mode-factory idiom — `settingsMode(...)`, `presentation: 'modal'`,
 * rendered through the {@link ./Overlay.js Overlay}). Several sections, navigated as one flat cursor
 * list of rows (j/k or arrows move the cursor; Enter acts on the focused row). The list is taller than
 * the screen, so it scrolls by cursor (a {@link VISIBLE_ROWS}-row window centred on the cursor, with
 * "↑ N more" / "↓ N more" affordances). The sections:
 *
 *  1. **Modifier** — a radio over `alt` / `ctrl` / `both`. The `ctrl` and `both` rows are *disabled*
 *     (un-selectable, dimmed) with an inline notice when the terminal cannot deliver ctrl chords
 *     ({@link ../terminal/capsStore.js kittySupported} === `false`). Selecting a row commits the
 *     modifier immediately (live: the dispatcher/footer/shim react at once).
 *  2. **Theme** — a select over the known {@link ../theme/palettes.js ThemeId}s. Moving the cursor
 *     onto a theme row **live-previews** it ({@link ../theme/themeStore.js setTheme} fires on cursor
 *     move, recoloring the whole UI under the modal); the preview is *committed* only on Save and is
 *     *reverted* to the persisted value on cancel/Esc — so browsing themes never persists a half-pick.
 *  3. **Pane gap** — a radio over `0`–`4` spaces of inter-pane border gap (live).
 *  4. **Harnesses** — collaborator (a radio over the 5 harnesses + a "(default)" row that clears the
 *     override) and crow (a checkbox pool over the 5 + a "reset to default" row; ≥1 must stay checked).
 *     The effective value is shown when no override is set.
 *  5. **LLM providers** — one row per provider (groq/cerebras/openrouter/local) with provenance
 *     ("set via env" / "set here (***)" / "not set"). Enter on a row enters *text-entry* for the
 *     api_key (or local's base_url): leaving `***` keeps the stored key, `""` clears.
 *  6. **Tiers & roles** — the tiers (built-in cheap/smart + user overrides) read-only as
 *     "name → provider/model", and a per-role radio (notetaker / crow_handler) binding a role to a
 *     tier name.
 *  7. **Key bindings** — the rebindable actions from {@link ../input/bindings.js ACTIONS} with their
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
 * Like the spawn wizard: the keymap carries structural keys (arrows, return, escape, backspace,
 * meta+u); the printable router lives in `onUncaptured`. In the normal state `onUncaptured` maps
 * `j`/`k` to cursor moves. In *capture* mode it consumes the next printable char as the rebind target
 * (rejection rules above). In *text-entry* mode (a provider field) it appends printables to the buffer
 * while Backspace/meta+u/Enter/Esc ride the keymap.
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
import type {
  LlmEnvWire,
  LlmProviderId,
  LlmTierWire,
  LlmWire,
  SettingsActions,
} from '../store/settings/settingsActions.js';
import { capsStore, type KittySupport, useKittySupport } from '../terminal/capsStore.js';
import { PALETTES, type ThemeId } from '../theme/palettes.js';
import { setTheme, useTheme } from '../theme/themeStore.js';
import { deleteLastChar, insertChar, TextInput } from './TextInput.js';

// Bring the dispatcher's `onUncaptured` augmentation into scope (the printable/capture router needs it).
import '../input/dispatcher.js';

/** Intent union — structural-key actions only. Printable chars (j/k + the captured rebind key) ride
 * `onUncaptured`, not the keymap. */
type SettingsIntent = 'cursorUp' | 'cursorDown' | 'confirm' | 'cancel' | 'backspace' | 'deleteAll';

/** The modifier choices in display order. */
const MODIFIERS: readonly Modifier[] = ['alt', 'ctrl', 'both'];

/** The pane-gap choices in display order — spaces between adjacent pane borders. `0` = flush
 * borders (the default); `1`–`4` add spacing. Mirrors the Python `TuiUserConfig.pane_gap` range
 * (`ge=0, le=4`). */
const GAP_OPTIONS: readonly number[] = [0, 1, 2, 3, 4];

/** The five valid harness ids, in display order (mirrors the Python `UserHarnessKind`; the backend
 * gated out `native_coding_crow`). Used by the collaborator radio + crow checkbox pool. */
const HARNESSES: readonly string[] = ['claude_code', 'codex', 'cursor', 'pi', 'antigravity'];

/** The four user-configurable LLM providers, in display order (mirrors `UserLlmConfig.providers`).
 * `local` is the OpenAI-compatible endpoint (no env-key flag; carries a base_url). */
const PROVIDERS: readonly LlmProviderId[] = ['groq', 'cerebras', 'openrouter', 'local'];

/** The providers that carry an env-key flag (`local` has none). */
const ENV_PROVIDERS: ReadonlySet<string> = new Set(['groq', 'cerebras', 'openrouter']);

/** The known roles a tier can be bound to (mirrors the Python role keys). */
const ROLES: readonly string[] = ['notetaker', 'crow_handler'];

/** Built-in tiers, present server-side even when `llm.tiers` is empty. Mirrors the Python
 * `BUILTIN_TIERS`; user-defined tiers of the same name override these (see `resolve_tier`). */
const BUILTIN_TIERS: Readonly<Record<string, LlmTierWire>> = {
  cheap: { provider: 'groq', model: 'openai/gpt-oss-120b', auto_free: true },
  smart: { provider: 'openrouter', model: 'anthropic/claude-sonnet-4-6' },
};

/** The merged tier map for display: built-ins overlaid by the user's same-name tiers. Order: built-ins
 * first (cheap, smart), then any extra user tiers in insertion order. */
function mergedTiers(llm: LlmWire): Array<[string, LlmTierWire]> {
  const out: Array<[string, LlmTierWire]> = [];
  const userTiers = llm.tiers ?? {};
  for (const name of Object.keys(BUILTIN_TIERS)) {
    const tier = userTiers[name] ?? BUILTIN_TIERS[name];
    if (tier !== undefined) {
      out.push([name, tier]);
    }
  }
  for (const [name, tier] of Object.entries(userTiers)) {
    if (!(name in BUILTIN_TIERS)) {
      out.push([name, tier]);
    }
  }
  return out;
}

/** The selectable tier names (for the per-role radio) — the merged tier map's keys. */
function tierNames(llm: LlmWire): readonly string[] {
  return mergedTiers(llm).map(([name]) => name);
}

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
  // Harnesses — collaborator radio (`value: null` = the "(default)" reset row); crow checkbox pool
  // (`value: null` = the "reset to default" row).
  | { readonly kind: 'collaborator'; readonly value: string | null }
  | { readonly kind: 'crow'; readonly value: string | null }
  // LLM providers — `field` distinguishes the api_key row from local's base_url row.
  | {
      readonly kind: 'provider';
      readonly provider: LlmProviderId;
      readonly field: 'api_key' | 'base_url';
    }
  // Tiers — read-only display rows (non-focusable, like a header).
  | { readonly kind: 'tier'; readonly name: string }
  // Roles — per-role radio over the tier names (`tier: null` is unused; the radio always has choices).
  | { readonly kind: 'role'; readonly role: string; readonly tier: string }
  | { readonly kind: 'binding'; readonly action: ActionId };

/** Build the flat row list (headers + selectable rows) in section order. Depends on the live `llm`
 * (the tier list + per-role tier choices are dynamic), so it is rebuilt whenever the draft changes. */
function buildRows(llm: LlmWire): readonly Row[] {
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
  // --- Harnesses ---
  rows.push({ kind: 'header', label: 'Collaborator harness' });
  rows.push({ kind: 'collaborator', value: null }); // the "(default)" reset row
  for (const value of HARNESSES) {
    rows.push({ kind: 'collaborator', value });
  }
  rows.push({ kind: 'header', label: 'Crow harnesses (pick ≥1)' });
  rows.push({ kind: 'crow', value: null }); // the "reset to default" row
  for (const value of HARNESSES) {
    rows.push({ kind: 'crow', value });
  }
  // --- LLM providers ---
  rows.push({ kind: 'header', label: 'LLM providers' });
  for (const provider of PROVIDERS) {
    rows.push({ kind: 'provider', provider, field: 'api_key' });
    if (provider === 'local') {
      rows.push({ kind: 'provider', provider, field: 'base_url' });
    }
  }
  // --- Tiers & roles ---
  rows.push({ kind: 'header', label: 'Tiers' });
  for (const [name] of mergedTiers(llm)) {
    rows.push({ kind: 'tier', name });
  }
  rows.push({ kind: 'header', label: 'Role → tier' });
  const choices = tierNames(llm);
  for (const role of ROLES) {
    for (const tier of choices) {
      rows.push({ kind: 'role', role, tier });
    }
  }
  // --- Key bindings ---
  rows.push({ kind: 'header', label: 'Key bindings' });
  for (const action of REBINDABLE) {
    rows.push({ kind: 'binding', action });
  }
  return rows;
}

/** Whether a row can hold the cursor (headers + read-only tier rows are skipped). A modifier row is
 * selectable even when disabled — the cursor can rest on it (to show the notice), but `confirm` is a
 * no-op there. */
function isSelectable(row: Row): boolean {
  return row.kind !== 'header' && row.kind !== 'tier';
}

/** A text-entry target — the provider field currently being edited (api_key / local base_url). */
interface EditTarget {
  readonly provider: LlmProviderId;
  readonly field: 'api_key' | 'base_url';
}

/** Mutable closure state — not React state. Mutated by `onIntent`/`onUncaptured`; `render` reads it. */
interface SettingsState {
  /** The live row list — rebuilt whenever the draft `llm` changes (tier/role rows are dynamic). */
  rows: readonly Row[];
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
  /** The draft collaborator-harness override (`null` = use the effective default). */
  collaboratorHarness: string | null;
  /** The daemon's live effective collaborator harness (display fallback when no override). */
  effectiveCollaborator: string;
  /** The draft crow-harness pool override (`null` = use the effective default); when set, ≥1 entry. */
  crowHarnesses: readonly string[] | null;
  /** The daemon's live effective crow-harness pool (display fallback when no override). */
  effectiveCrow: readonly string[];
  /** The draft LLM config (masked api keys). Drives the provider/tier/role rows. */
  llm: LlmWire;
  /** Whether each env-flagged provider's key is present in the daemon's environment (display only). */
  llmEnv: LlmEnvWire;
  /** The provider field being text-edited, or `null` when not editing. */
  editing: EditTarget | null;
  /** The in-progress text-entry buffer (the api_key / base_url being typed). */
  editValue: string;
  /** The action currently capturing its next-key rebind, or `null` when not capturing. */
  capturing: ActionId | null;
  /** The transient inline notice (rejection reason / hint), or `null`. */
  notice: string | null;
}

/** Find the first selectable row index at or after `from` (wrapping). Used to seed the cursor and to
 * skip header/read-only rows during navigation. */
function firstSelectableFrom(rows: readonly Row[], from: number): number {
  for (let i = 0; i < rows.length; i++) {
    const idx = (from + i) % rows.length;
    const row = rows[idx];
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
    readonly collaboratorHarness?: string | null;
    readonly effectiveCollaborator?: string;
    readonly crowHarnesses?: readonly string[] | null;
    readonly effectiveCrow?: readonly string[];
    readonly llm?: LlmWire;
    readonly llmEnv?: LlmEnvWire;
  },
  opts: SettingsModeOptions = {},
): Mode<SettingsIntent> {
  const id = SETTINGS_MODE_ID;

  const initialLlm: LlmWire = current.llm ?? {};
  const initialRows = buildRows(initialLlm);
  const s: SettingsState = {
    rows: initialRows,
    cursor: firstSelectableFrom(initialRows, 0),
    modifier: current.modifier,
    persistedTheme: current.theme,
    theme: current.theme,
    paneGap: current.paneGap,
    overrides: { ...current.keyOverrides },
    collaboratorHarness: current.collaboratorHarness ?? null,
    effectiveCollaborator: current.effectiveCollaborator ?? 'claude_code',
    crowHarnesses: current.crowHarnesses ?? null,
    effectiveCrow: current.effectiveCrow ?? ['claude_code'],
    llm: initialLlm,
    llmEnv: current.llmEnv ?? { groq: false, cerebras: false, openrouter: false },
    editing: null,
    editValue: '',
    capturing: null,
    notice: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((f) => f.mode.id === id);
    if (frame !== undefined) {
      modes.getState().enter(frame.mode);
    }
  }

  /** Move the cursor by `delta`, skipping header/read-only rows (wrapping). */
  function moveCursor(delta: number): void {
    const len = s.rows.length;
    let idx = s.cursor;
    for (let step = 0; step < len; step++) {
      idx = (idx + delta + len) % len;
      const row = s.rows[idx];
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

  /** Commit the draft collaborator harness. `null` clears the override (back to the effective default). */
  function selectCollaborator(value: string | null): void {
    s.collaboratorHarness = value;
    void actions.update({ collaborator_harness: value });
    s.notice = null;
    refresh();
  }

  /** Toggle a crow harness in/out of the draft pool, or (value=null) reset to the effective default.
   * Enforces ≥1 checked — unchecking the last entry is blocked with a notice. The effective pool is
   * the starting point when no override is set yet (so a first toggle edits the live default). */
  function toggleCrow(value: string | null): void {
    if (value === null) {
      s.crowHarnesses = null;
      void actions.update({ crow_harnesses: null });
      s.notice = null;
      refresh();
      return;
    }
    const current = s.crowHarnesses ?? [...s.effectiveCrow];
    const checked = current.includes(value);
    if (checked && current.length === 1) {
      s.notice = 'At least one crow harness must stay selected';
      refresh();
      return;
    }
    const next = checked ? current.filter((h) => h !== value) : [...current, value];
    s.crowHarnesses = next;
    void actions.update({ crow_harnesses: next });
    s.notice = null;
    refresh();
  }

  /** Bind a role to a tier name (deep-merge; the server cannot delete a role key, so there is no
   * "unset"). Refreshes the draft llm + rebuilds rows so the role radio reflects the new mapping. */
  function selectRole(role: string, tier: string): void {
    const nextRoles = { ...(s.llm.roles ?? {}), [role]: tier };
    s.llm = { ...s.llm, roles: nextRoles };
    rebuildRows();
    void actions.update({ llm: { roles: { [role]: tier } } });
    s.notice = null;
    refresh();
  }

  /** Enter text-entry for a provider field, seeding the buffer with the stored value (masked `***` for
   * a set api_key — leaving it submits `***` = unchanged; the base_url shows the stored URL). */
  function beginEdit(provider: LlmProviderId, field: 'api_key' | 'base_url'): void {
    const stored = s.llm.providers?.[provider]?.[field];
    s.editing = { provider, field };
    s.editValue = stored ?? '';
    s.notice =
      field === 'api_key'
        ? 'Type the API key (leave as *** to keep, empty to clear). Enter to save, Esc to cancel.'
        : 'Type the base URL (empty to clear). Enter to save, Esc to cancel.';
    refresh();
  }

  /** Commit the text-entry buffer to the provider field. For api_key, `***` means "unchanged"; `''`
   * clears. Refreshes the draft llm from the reply (re-masking) and rebuilds rows. */
  function commitEdit(): void {
    if (s.editing === null) {
      return;
    }
    const { provider, field } = s.editing;
    const value = s.editValue;
    const nextProvider = { ...(s.llm.providers?.[provider] ?? {}), [field]: value };
    s.llm = {
      ...s.llm,
      providers: { ...(s.llm.providers ?? {}), [provider]: nextProvider },
    };
    s.editing = null;
    s.editValue = '';
    s.notice = null;
    rebuildRows();
    void actions.update({ llm: { providers: { [provider]: { [field]: value } } } });
    refresh();
  }

  /** Rebuild the live row list from the draft llm, clamping the cursor onto a still-selectable row. */
  function rebuildRows(): void {
    s.rows = buildRows(s.llm);
    if (s.cursor >= s.rows.length || !isSelectable(s.rows[s.cursor] as Row)) {
      s.cursor = firstSelectableFrom(s.rows, Math.min(s.cursor, s.rows.length - 1));
    }
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
    const row = s.rows[s.cursor];
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
      case 'collaborator':
        selectCollaborator(row.value);
        break;
      case 'crow':
        toggleCrow(row.value);
        break;
      case 'provider':
        beginEdit(row.provider, row.field);
        break;
      case 'role':
        selectRole(row.role, row.tier);
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
    if (s.editing !== null) {
      // Esc during text-entry cancels the edit only (stay in the modal; no commit).
      s.editing = null;
      s.editValue = '';
      s.notice = null;
      refresh();
      return;
    }
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
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { input: 'u', key: { meta: true } }, intent: 'deleteAll', description: 'clear' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'cursorUp':
          // Cursor moves are inert while capturing a rebind or editing a provider field.
          if (s.capturing === null && s.editing === null) {
            moveCursor(-1);
          }
          break;
        case 'cursorDown':
          if (s.capturing === null && s.editing === null) {
            moveCursor(1);
          }
          break;
        case 'confirm':
          // Enter commits an in-progress text edit; otherwise acts on the focused row.
          if (s.editing !== null) {
            commitEdit();
          } else if (s.capturing === null) {
            confirm();
          }
          break;
        case 'cancel':
          dismiss();
          break;
        case 'backspace':
          if (s.editing !== null) {
            s.editValue = deleteLastChar(s.editValue);
            refresh();
          }
          break;
        case 'deleteAll':
          if (s.editing !== null) {
            s.editValue = '';
            refresh();
          }
          break;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      // Text-entry mode (a provider api_key / base_url): printable chars extend the buffer. Structural
      // keys (Enter/Esc/Backspace) ride the keymap.
      if (s.editing !== null) {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
          return false;
        }
        s.editValue = insertChar(s.editValue, input);
        refresh();
        return true;
      }
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
  const view = rowWindow(s.rows, s.cursor);

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
        {view.before > 0 && (
          <Box flexShrink={0}>
            <Text dimColor>{`  ↑ ${view.before} more`}</Text>
          </Box>
        )}
        {view.rows.map(({ row, index }) => (
          <RowView
            key={rowKey(row)}
            row={row}
            focused={index === s.cursor}
            state={s}
            theme={theme}
            ctrlAvailable={ctrlAvailable}
          />
        ))}
        {view.after > 0 && (
          <Box flexShrink={0}>
            <Text dimColor>{`  ↓ ${view.after} more`}</Text>
          </Box>
        )}
      </Box>

      {/* The ctrl-unsupported notice is always shown under the modifier section when ctrl can't be
          delivered, so the user understands why the ctrl/both rows are disabled. */}
      {!ctrlAvailable && (
        <Box marginTop={1} flexShrink={0}>
          <Text color={theme.muted}>{CTRL_UNSUPPORTED_NOTICE}</Text>
        </Box>
      )}

      {s.notice !== null && (
        <Box marginTop={1} flexShrink={0}>
          <Text color={theme.warning}>{s.notice}</Text>
        </Box>
      )}

      <Box marginTop={1} flexShrink={0}>
        <Text dimColor>j/k: navigate · enter: select/rebind · esc: close</Text>
      </Box>
    </Box>
  );
}

/** The maximum number of section rows shown at once — a scroll-by-cursor window so the modal stays
 * usable at 80x24 once all sections are present (the row list is far taller than the screen). */
const VISIBLE_ROWS = 16;

/** Compute the visible row window around the cursor. Returns the slice (with original indices, so
 * focus math stays correct) plus the count of rows hidden above/below (shown as "↑ N more"). */
function rowWindow(
  rows: readonly Row[],
  cursor: number,
): { rows: Array<{ row: Row; index: number }>; before: number; after: number } {
  if (rows.length <= VISIBLE_ROWS) {
    return {
      rows: rows.map((row, index) => ({ row, index })),
      before: 0,
      after: 0,
    };
  }
  // Centre the window on the cursor, clamped to the ends.
  const half = Math.floor(VISIBLE_ROWS / 2);
  let start = Math.max(0, cursor - half);
  const end = Math.min(rows.length, start + VISIBLE_ROWS);
  start = Math.max(0, end - VISIBLE_ROWS);
  const slice: Array<{ row: Row; index: number }> = [];
  for (let i = start; i < end; i++) {
    const row = rows[i];
    if (row !== undefined) {
      slice.push({ row, index: i });
    }
  }
  return { rows: slice, before: start, after: rows.length - end };
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
    case 'collaborator':
      return `collab:${row.value ?? 'default'}`;
    case 'crow':
      return `crow:${row.value ?? 'default'}`;
    case 'provider':
      return `provider:${row.provider}:${row.field}`;
    case 'tier':
      return `tier:${row.name}`;
    case 'role':
      return `role:${row.role}:${row.tier}`;
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
      <Box marginTop={1} flexShrink={0}>
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
      <Box flexShrink={0}>
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
      <Box flexShrink={0}>
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
      <Box flexShrink={0}>
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

  if (row.kind === 'collaborator') {
    // The "(default)" row (value null) selects the no-override state; the harness rows are a radio.
    const isDefaultRow = row.value === null;
    const selected = isDefaultRow
      ? s.collaboratorHarness === null
      : s.collaboratorHarness === row.value;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    const label = isDefaultRow ? `(default) ${s.effectiveCollaborator}` : (row.value ?? '');
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {label}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'crow') {
    // The "reset to default" row clears the override; the harness rows are a checkbox pool. The
    // displayed checked-set is the override, or the effective default when no override is set.
    const isResetRow = row.value === null;
    const pool = s.crowHarnesses ?? s.effectiveCrow;
    const color = focused ? theme.warning : theme.text;
    if (isResetRow) {
      const usingDefault = s.crowHarnesses === null;
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {usingDefault ? '(•) ' : '( ) '}
            {`reset to default (${s.effectiveCrow.join(', ')})`}
          </Text>
        </Box>
      );
    }
    const checked = pool.includes(row.value as string);
    const mark = checked ? '[x] ' : '[ ] ';
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {row.value}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'provider') {
    const stored = s.llm.providers?.[row.provider];
    const editing = s.editing?.provider === row.provider && s.editing?.field === row.field;
    const color = focused ? theme.warning : theme.text;
    if (row.field === 'base_url') {
      // local base_url (no env flag).
      const value = stored?.base_url;
      const provenance = value ? value : 'not set';
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {`${row.provider} base_url`}
          </Text>
          <Text color={theme.muted}>{'  '}</Text>
          {editing ? (
            <TextInput value={s.editValue} placeholder="https://…" focused color={theme.text} />
          ) : (
            <Text color={theme.muted}>{provenance}</Text>
          )}
        </Box>
      );
    }
    // api_key row. Provenance: env > stored (masked) > not set.
    const viaEnv = ENV_PROVIDERS.has(row.provider) && s.llmEnv[row.provider as keyof LlmEnvWire];
    const storedKey = stored?.api_key;
    const provenance = viaEnv ? 'set via env' : storedKey ? `set here (${storedKey})` : 'not set';
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {`${row.provider} api_key`}
        </Text>
        <Text color={theme.muted}>{'  '}</Text>
        {editing ? (
          <TextInput value={s.editValue} placeholder="(api key)" focused color={theme.text} />
        ) : (
          <Text color={viaEnv ? theme.muted : theme.text}>{provenance}</Text>
        )}
      </Box>
    );
  }

  if (row.kind === 'tier') {
    // Read-only display: "name → provider/model" (+ a free marker for auto_free tiers).
    const tier = mergedTiers(s.llm).find(([name]) => name === row.name)?.[1];
    const desc = tier
      ? `${tier.provider}/${tier.model}${tier.auto_free ? ' (auto-free)' : ''}`
      : '?';
    return (
      <Box flexShrink={0}>
        <Text color={theme.text}>
          {'  '}
          {row.name}
          <Text color={theme.muted}>{` → ${desc}`}</Text>
        </Text>
      </Box>
    );
  }

  if (row.kind === 'role') {
    const mapped = s.llm.roles?.[row.role];
    const selected = mapped === row.tier;
    // Show "(default)" once per role group (on its first tier row) when no mapping exists yet.
    const noMapping = mapped === undefined;
    const choices = tierNames(s.llm);
    const isFirstTier = choices[0] === row.tier;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {`${row.role}: ${row.tier}`}
          {noMapping && isFirstTier ? ' (no mapping yet → default)' : ''}
        </Text>
      </Box>
    );
  }

  // binding row
  const capturing = s.capturing === row.action;
  const label = bindingLabel(row.action, s.modifier, s.overrides);
  const color = focused ? theme.warning : theme.text;
  return (
    <Box flexShrink={0}>
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
