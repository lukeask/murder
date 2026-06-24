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
 *     across theme rows **live-previews** them ({@link ../theme/themeStore.js setTheme} fires on
 *     cursor move, recoloring the whole UI under the modal); the preview is *committed* only on
 *     Enter and is *reverted* to the persisted value on cancel/Esc or when the cursor leaves the
 *     theme section — so browsing themes never persists a half-pick or affects later navigation.
 *  3. **Pane gap** — a radio over `0`–`4` spaces of inter-pane border gap (live).
 *  4. **Harnesses** — planner (a radio over the 5 harnesses + a "(default)" row that clears the
 *     override) and crow (a checkbox pool over the 5 + a "reset to default" row; ≥1 must stay checked).
 *     The effective value is shown when no override is set.
 *  5. **LLM providers** — one row per provider (groq/cerebras first; openrouter/local opt-in)
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
import { useContext, useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import { AppStoreContext } from '../hooks/useAppStore.js';
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
  StartupRogueWire,
} from '../store/settings/settingsActions.js';
import type { DefaultChatViewMode } from '../store/settings/settingsSlice.js';
import type { AppStore } from '../store/store.js';
import type { TemplateRecord } from '../store/templates/templatesSlice.js';
import { capsStore, type KittySupport, useKittySupport } from '../terminal/capsStore.js';
import { PALETTES, type ThemeId } from '../theme/palettes.js';
import { setTheme, useTheme } from '../theme/themeStore.js';
import { deleteLastChar, insertChar, TextInput } from './TextInput.js';

// Bring the dispatcher's `onUncaptured` augmentation into scope (the printable/capture router needs it).
import '../input/dispatcher.js';

/** Intent union — structural-key actions only. Printable chars (j/k + the captured rebind key) ride
 * `onUncaptured`, not the keymap. */
type SettingsIntent = 'cursorUp' | 'cursorDown' | 'confirm' | 'cancel' | 'backspace' | 'deleteAll';

/** A valid template name: non-empty, `[A-Za-z0-9_-]+` (mirrors the backend's `^[A-Za-z0-9_-]+$`). */
const TEMPLATE_NAME_RE = /^[A-Za-z0-9_-]+$/;

/** The modifier choices in display order. */
const MODIFIERS: readonly Modifier[] = ['alt', 'ctrl', 'both'];

/** The pane-gap choices in display order — spaces between adjacent pane borders. `0` = flush
 * borders (the default); `1`–`4` add spacing. Mirrors the Python `TuiUserConfig.pane_gap` range
 * (`ge=0, le=4`). */
const GAP_OPTIONS: readonly number[] = [0, 1, 2, 3, 4];

/** The five valid harness ids, in display order (mirrors the Python `UserHarnessKind`; the backend
 * gated out `native_coding_crow`). Used by the planner radio + crow checkbox pool. */
const HARNESSES: readonly string[] = ['claude_code', 'codex', 'cursor', 'pi', 'antigravity'];

/** Startup-Rogue model choices per harness (a `''` "default" lets the adapter pick its own). A static
 * mirror of the spawn flow's per-harness model lists — kept local so the settings modal needs no live
 * model-snapshot wiring; the daemon accepts any string, so a stale entry just spawns that model. */
const STARTUP_ROGUE_MODELS: Readonly<Record<string, readonly string[]>> = {
  claude_code: ['', 'opus', 'sonnet', 'haiku', 'fable'],
  codex: ['', 'gpt-5.5', 'gpt-5.4', 'gpt-5.3-codex', 'gpt-5.2'],
  cursor: [''],
  pi: [''],
  antigravity: [''],
};

/** Startup-Rogue reasoning-effort choices per harness (mirrors the spawn wizard's effort options;
 * harnesses with no effort concept map to `[]`). */
const STARTUP_ROGUE_EFFORTS: Readonly<Record<string, readonly string[]>> = {
  claude_code: ['low', 'medium', 'high', 'xhigh', 'max'],
  codex: ['low', 'medium', 'high', 'xhigh'],
  cursor: [],
  pi: [],
  antigravity: [],
};

/** Pick the default effort for a harness when one is switched on (prefer `medium`, else the first
 * option, else `null` for harnesses with no effort concept). */
function defaultEffortFor(harness: string): string | null {
  const efforts = STARTUP_ROGUE_EFFORTS[harness] ?? [];
  if (efforts.length === 0) {
    return null;
  }
  return efforts.includes('medium') ? 'medium' : (efforts[0] ?? null);
}

/** The four user-configurable LLM providers, in display order (mirrors `UserLlmConfig.providers`).
 * `local` is the OpenAI-compatible endpoint (no env-key flag; carries a base_url). */
const PROVIDERS: readonly LlmProviderId[] = ['groq', 'cerebras', 'openrouter', 'local'];

/** The providers that carry an env-key flag (`local` has none). */
const ENV_PROVIDERS: ReadonlySet<string> = new Set(['groq', 'cerebras', 'openrouter']);

/** The known roles a tier can be bound to (mirrors the Python role keys). */
const ROLES: readonly string[] = ['notetaker', 'crow_handler', 'codebase_map'];

/** Built-in tiers, present server-side even when `llm.tiers` is empty. Mirrors the Python
 * `BUILTIN_TIERS`; user-defined tiers of the same name override these (see `resolve_tier`). */
const BUILTIN_TIERS: Readonly<Record<string, LlmTierWire>> = {
  cheap: { provider: 'groq', model: 'openai/gpt-oss-120b', auto_free: true },
  smart: { provider: 'cerebras', model: 'openai/gpt-oss-120b' },
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
  // Vim mode — a radio over on (true) / off (false).
  | { readonly kind: 'vim'; readonly value: boolean }
  // Default chat view — a radio over verbose / condensed (tmux is cycle-only, not a default).
  | { readonly kind: 'chatView'; readonly value: DefaultChatViewMode }
  // Startup Rogue — an "off" row, a harness radio, then (when on) a model radio + effort radio.
  | { readonly kind: 'startupRogue'; readonly field: 'off' }
  | { readonly kind: 'startupRogue'; readonly field: 'harness'; readonly value: string }
  | { readonly kind: 'startupRogue'; readonly field: 'model'; readonly value: string }
  | { readonly kind: 'startupRogue'; readonly field: 'effort'; readonly value: string }
  // Harnesses — planner radio (`value: null` = the "(default)" reset row); crow checkbox pool
  // (`value: null` = the "reset to default" row). The dormant collaborator radio stays implemented
  // below, but its rows are commented out in buildRows while collaborator is not user-facing.
  | { readonly kind: 'collaborator'; readonly value: string | null }
  | { readonly kind: 'planner'; readonly value: string | null }
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
  // Templates — one selectable row per saved template (rename/delete), or a non-selectable empty hint.
  | { readonly kind: 'template'; readonly name: string }
  | { readonly kind: 'templateEmpty' }
  | { readonly kind: 'binding'; readonly action: ActionId };

/** Build the flat row list (headers + selectable rows) in section order. Depends on the live `llm`
 * (the tier list + per-role tier choices are dynamic), so it is rebuilt whenever the draft changes. */
function buildRows(
  llm: LlmWire,
  startupRogue: StartupRogueWire | null,
  templates: readonly TemplateRecord[],
): readonly Row[] {
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
  // --- Vim mode ---
  rows.push({ kind: 'header', label: 'Vim mode' });
  rows.push({ kind: 'vim', value: true });
  rows.push({ kind: 'vim', value: false });
  // --- Default chat view (TUIchat-3) — tmux is reachable only via the per-pane cycle key. ---
  rows.push({ kind: 'header', label: 'Default chat view' });
  rows.push({ kind: 'chatView', value: 'verbose' });
  rows.push({ kind: 'chatView', value: 'condensed' });
  // --- Startup Rogue (auto-spawned on boot) ---
  rows.push({ kind: 'header', label: 'Startup Rogue' });
  rows.push({ kind: 'startupRogue', field: 'off' });
  for (const value of HARNESSES) {
    rows.push({ kind: 'startupRogue', field: 'harness', value });
  }
  // Model + effort sub-rows only when a rogue is configured (they depend on the chosen harness).
  if (startupRogue !== null) {
    for (const value of STARTUP_ROGUE_MODELS[startupRogue.harness] ?? ['']) {
      rows.push({ kind: 'startupRogue', field: 'model', value });
    }
    for (const value of STARTUP_ROGUE_EFFORTS[startupRogue.harness] ?? []) {
      rows.push({ kind: 'startupRogue', field: 'effort', value });
    }
  }
  // --- Harnesses ---
  // Collaborator is dormant. Keep these rows commented so the setting can be restored locally when
  // collaborator returns to the active workflow.
  // rows.push({ kind: 'header', label: 'Collaborator harness' });
  // rows.push({ kind: 'collaborator', value: null }); // the "(default)" reset row
  // for (const value of HARNESSES) {
  //   rows.push({ kind: 'collaborator', value });
  // }
  rows.push({ kind: 'header', label: 'Planning agent harness' });
  rows.push({ kind: 'planner', value: null }); // the "(default)" reset row
  for (const value of HARNESSES) {
    rows.push({ kind: 'planner', value });
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
  // --- Templates (browse / preview / rename / delete; creation is the chat `:save` command) ---
  rows.push({ kind: 'header', label: 'Templates' });
  if (templates.length === 0) {
    rows.push({ kind: 'templateEmpty' });
  } else {
    for (const t of templates) {
      rows.push({ kind: 'template', name: t.name });
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
  return row.kind !== 'header' && row.kind !== 'tier' && row.kind !== 'templateEmpty';
}

/** A text-entry target — either a provider field (api_key / local base_url) or a template rename. */
type EditTarget =
  | {
      readonly kind: 'provider';
      readonly provider: LlmProviderId;
      readonly field: 'api_key' | 'base_url';
    }
  | { readonly kind: 'templateRename'; readonly name: string };

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
  /** The draft vim-mode flag (committed via `update` on selection). */
  vimMode: boolean;
  /** The draft default chat view mode (TUIchat-3; committed via `update` on selection). Only
   * verbose/condensed are settable — tmux is reachable only via the per-pane cycle key. */
  defaultChatViewMode: DefaultChatViewMode;
  /** The draft Startup Rogue (`null` = off). Drives the model/effort sub-rows + persisted on change. */
  startupRogue: StartupRogueWire | null;
  /** The draft per-action key overrides (`ActionId -> key char`). */
  overrides: Record<string, string>;
  /** The draft collaborator-harness override (`null` = use the effective default). */
  collaboratorHarness: string | null;
  /** The daemon's live effective collaborator harness (display fallback when no override). */
  effectiveCollaborator: string;
  /** The draft planning-agent harness override (`null` = use the effective default). */
  plannerHarness: string | null;
  /** The daemon's live effective planning-agent harness (display fallback when no override). */
  effectivePlanner: string;
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
  /** The live saved templates (browse/preview/rename/delete source). Synced from the app store. */
  templates: readonly TemplateRecord[];
  /** The templates action handle (rename/remove). Synced from the app store. */
  templateActions: { remove(name: string): void; rename(oldName: string, newName: string): void };
  /** The template whose body is previewed under the cursor, or `null` when the cursor is elsewhere. */
  previewTemplate: TemplateRecord | null;
  /** The template name pending a delete confirm ("(y/n)"), or `null` when not confirming. */
  confirmingDelete: string | null;
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
    readonly vimMode?: boolean;
    readonly defaultChatViewMode?: DefaultChatViewMode;
    readonly startupRogue?: StartupRogueWire | null;
    readonly keyOverrides: Record<string, string>;
    readonly collaboratorHarness?: string | null;
    readonly effectiveCollaborator?: string;
    readonly plannerHarness?: string | null;
    readonly effectivePlanner?: string;
    readonly crowHarnesses?: readonly string[] | null;
    readonly effectiveCrow?: readonly string[];
    readonly llm?: LlmWire;
    readonly llmEnv?: LlmEnvWire;
    readonly templates?: readonly TemplateRecord[];
    readonly templateActions?: {
      remove(name: string): void;
      rename(oldName: string, newName: string): void;
    };
  },
  opts: SettingsModeOptions = {},
): Mode<SettingsIntent> {
  const id = SETTINGS_MODE_ID;

  const initialLlm: LlmWire = current.llm ?? {};
  const initialStartupRogue: StartupRogueWire | null = current.startupRogue ?? null;
  const initialTemplates: readonly TemplateRecord[] = current.templates ?? [];
  const initialRows = buildRows(initialLlm, initialStartupRogue, initialTemplates);
  const s: SettingsState = {
    rows: initialRows,
    cursor: firstSelectableFrom(initialRows, 0),
    modifier: current.modifier,
    persistedTheme: current.theme,
    theme: current.theme,
    paneGap: current.paneGap,
    vimMode: current.vimMode ?? false,
    defaultChatViewMode: current.defaultChatViewMode ?? 'verbose',
    startupRogue: initialStartupRogue,
    overrides: { ...current.keyOverrides },
    collaboratorHarness: current.collaboratorHarness ?? null,
    effectiveCollaborator: current.effectiveCollaborator ?? 'claude_code',
    plannerHarness: current.plannerHarness ?? null,
    effectivePlanner: current.effectivePlanner ?? 'claude_code',
    crowHarnesses: current.crowHarnesses ?? null,
    effectiveCrow: current.effectiveCrow ?? ['claude_code'],
    llm: initialLlm,
    llmEnv: current.llmEnv ?? { groq: false, cerebras: false, openrouter: false },
    editing: null,
    editValue: '',
    capturing: null,
    templates: initialTemplates,
    templateActions: current.templateActions ?? { remove() {}, rename() {} },
    previewTemplate: null,
    confirmingDelete: null,
    notice: null,
  };

  function refresh(): void {
    const frame = modes.getState().stack.find((f) => f.mode.id === id);
    if (frame !== undefined) {
      modes.getState().enter(frame.mode);
    }
  }

  /** Drop an uncommitted theme preview when navigation leaves the theme rows. */
  function restoreThemePreview(): void {
    if (s.theme !== s.persistedTheme) {
      s.theme = s.persistedTheme;
      setTheme(s.persistedTheme);
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
        const prev = s.rows[s.cursor];
        s.cursor = idx;
        // Live theme preview: landing the cursor on a theme row applies it immediately (committed on
        // Enter, reverted on cancel or when the cursor leaves the theme section).
        if (row.kind === 'theme') {
          s.theme = row.value;
          setTheme(row.value);
        } else if (prev?.kind === 'theme') {
          restoreThemePreview();
        }
        // Live template preview: stash the body when the cursor lands on a template row, clear it when
        // it leaves the section (read-only — never mutates the registry).
        if (row.kind === 'template') {
          s.previewTemplate = s.templates.find((t) => t.name === row.name) ?? null;
        } else {
          s.previewTemplate = null;
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

  /** Commit the draft vim-mode flag (optimistic update). WS-E reads `settings.vimMode` to switch the
   * chat input between plain and vim editing. */
  function selectVim(value: boolean): void {
    s.vimMode = value;
    void actions.update({ vim_mode: value });
    s.notice = null;
    refresh();
  }

  /** Commit the draft default chat view mode (TUIchat-3, optimistic update). A pane with no per-pane
   * override renders in this mode (`conversations.paneViewModes[id] ?? defaultChatViewMode`). */
  function selectChatView(value: DefaultChatViewMode): void {
    s.defaultChatViewMode = value;
    void actions.update({ default_chat_view_mode: value });
    s.notice = null;
    refresh();
  }

  /** Turn the Startup Rogue off (clear it). Rebuilds rows (the model/effort sub-rows drop away). */
  function selectStartupRogueOff(): void {
    s.startupRogue = null;
    rebuildRows();
    void actions.update({ startup_rogue: null });
    s.notice = null;
    refresh();
  }

  /** Pick the Startup Rogue's harness (turning it on if it was off). Resets the model to the adapter
   * default and the effort to the harness default when the harness changes; rebuilds the model/effort
   * sub-rows for the new harness. */
  function selectStartupRogueHarness(harness: string): void {
    const prev = s.startupRogue;
    const same = prev !== null && prev.harness === harness;
    const efforts = STARTUP_ROGUE_EFFORTS[harness] ?? [];
    const next: StartupRogueWire = {
      harness,
      model: same ? prev.model : '',
      effort:
        same && prev.effort !== null && efforts.includes(prev.effort)
          ? prev.effort
          : defaultEffortFor(harness),
    };
    s.startupRogue = next;
    rebuildRows();
    void actions.update({ startup_rogue: next });
    s.notice = null;
    refresh();
  }

  /** Pick the Startup Rogue's model (`''` = the adapter default). No row-structure change. */
  function selectStartupRogueModel(model: string): void {
    if (s.startupRogue === null) {
      return;
    }
    const next: StartupRogueWire = { ...s.startupRogue, model };
    s.startupRogue = next;
    void actions.update({ startup_rogue: next });
    s.notice = null;
    refresh();
  }

  /** Pick the Startup Rogue's reasoning effort. No row-structure change. */
  function selectStartupRogueEffort(effort: string): void {
    if (s.startupRogue === null) {
      return;
    }
    const next: StartupRogueWire = { ...s.startupRogue, effort };
    s.startupRogue = next;
    void actions.update({ startup_rogue: next });
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

  /** Commit the draft planning-agent harness. `null` clears the override. */
  function selectPlanner(value: string | null): void {
    s.plannerHarness = value;
    void actions.update({ planner_harness: value });
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
    s.editing = { kind: 'provider', provider, field };
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
    if (s.editing.kind === 'templateRename') {
      commitTemplateRename();
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

  /** Begin an inline rename of the template under the cursor, seeding the buffer with its name. */
  function beginTemplateRename(name: string): void {
    s.editing = { kind: 'templateRename', name };
    s.editValue = name;
    s.notice = 'Rename template. Enter to save, Esc to cancel.';
    refresh();
  }

  /** Commit a template rename: validate the new name (non-empty, `[A-Za-z0-9_-]+`, no collision with a
   * different existing template), then dispatch `rename` + rebuild. On a bad name, keep editing. */
  function commitTemplateRename(): void {
    if (s.editing === null || s.editing.kind !== 'templateRename') {
      return;
    }
    const oldName = s.editing.name;
    const newName = s.editValue.trim();
    if (newName === '') {
      s.notice = 'Template name cannot be empty';
      refresh();
      return;
    }
    if (!TEMPLATE_NAME_RE.test(newName)) {
      s.notice = `"${newName}" is invalid — use letters, digits, _ or - only`;
      refresh();
      return;
    }
    if (newName !== oldName && s.templates.some((t) => t.name === newName)) {
      s.notice = `A template named "${newName}" already exists`;
      refresh();
      return;
    }
    s.editing = null;
    s.editValue = '';
    s.previewTemplate = null;
    s.notice = null;
    if (newName !== oldName) {
      s.templateActions.rename(oldName, newName);
    }
    rebuildRows();
    refresh();
  }

  /** Enter the delete-confirm state for the template under the cursor (the next `y` removes it). */
  function beginTemplateDelete(name: string): void {
    s.confirmingDelete = name;
    s.notice = `delete "${name}"? (y/n)`;
    refresh();
  }

  /** Apply / cancel a pending template delete. `confirmed` removes it + rebuilds; otherwise cancels. */
  function resolveTemplateDelete(confirmed: boolean): void {
    const name = s.confirmingDelete;
    s.confirmingDelete = null;
    s.notice = null;
    if (confirmed && name !== null) {
      s.previewTemplate = null;
      s.templateActions.remove(name);
      rebuildRows();
    }
    refresh();
  }

  /** Sync the live templates registry + action handle from the app store into the closure state. Called
   * by the render component when the store's `templates.items` (or actions) change, so the section
   * tracks `:save`/external edits live. Rebuilds rows only when the item list actually changed. */
  function syncTemplates(
    items: readonly TemplateRecord[],
    templateActions: {
      remove(name: string): void;
      rename(oldName: string, newName: string): void;
    },
  ): void {
    s.templateActions = templateActions;
    const changed =
      items.length !== s.templates.length ||
      items.some((t, i) => t.name !== s.templates[i]?.name || t.body !== s.templates[i]?.body);
    if (!changed) {
      return;
    }
    s.templates = items;
    // Keep an active preview in sync with the new body (or drop it if the template is gone).
    if (s.previewTemplate !== null) {
      s.previewTemplate = items.find((t) => t.name === s.previewTemplate?.name) ?? null;
    }
    rebuildRows();
    refresh();
  }

  /** Rebuild the live row list from the draft llm, clamping the cursor onto a still-selectable row. */
  function rebuildRows(): void {
    s.rows = buildRows(s.llm, s.startupRogue, s.templates);
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
        s.theme = row.value;
        s.persistedTheme = row.value;
        setTheme(row.value);
        void actions.update({ theme: row.value });
        s.notice = 'Theme saved';
        refresh();
        break;
      case 'gap':
        selectGap(row.value);
        break;
      case 'vim':
        selectVim(row.value);
        break;
      case 'chatView':
        selectChatView(row.value);
        break;
      case 'startupRogue':
        if (row.field === 'off') {
          selectStartupRogueOff();
        } else if (row.field === 'harness') {
          selectStartupRogueHarness(row.value);
        } else if (row.field === 'model') {
          selectStartupRogueModel(row.value);
        } else {
          selectStartupRogueEffort(row.value);
        }
        break;
      case 'collaborator':
        selectCollaborator(row.value);
        break;
      case 'planner':
        selectPlanner(row.value);
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
      case 'template':
        beginTemplateRename(row.name);
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
    if (s.confirmingDelete !== null) {
      // Esc during a delete-confirm cancels the delete only (stay in the modal).
      resolveTemplateDelete(false);
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
          // Cursor moves are inert while capturing, editing, or awaiting a delete confirm.
          if (s.capturing === null && s.editing === null && s.confirmingDelete === null) {
            moveCursor(-1);
          }
          break;
        case 'cursorDown':
          if (s.capturing === null && s.editing === null && s.confirmingDelete === null) {
            moveCursor(1);
          }
          break;
        case 'confirm':
          // Enter commits an in-progress text edit; otherwise acts on the focused row. Inert while a
          // delete confirm is pending (it only accepts y/n via onUncaptured, or Esc to cancel).
          if (s.editing !== null) {
            commitEdit();
          } else if (s.capturing === null && s.confirmingDelete === null) {
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
      // Delete-confirm mode: the next `y` removes the template; `n` cancels (Esc also cancels via the
      // keymap → dismiss). Any other printable is swallowed (the confirm stays up).
      if (s.confirmingDelete !== null) {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
          return false;
        }
        if (input === 'y' || input === 'Y') {
          resolveTemplateDelete(true);
        } else if (input === 'n' || input === 'N') {
          resolveTemplateDelete(false);
        }
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
      // `d` on a template row opens a delete confirm (an unobtrusive key; only acts on a template).
      if (input === 'd') {
        const row = s.rows[s.cursor];
        if (row?.kind === 'template') {
          beginTemplateDelete(row.name);
          return true;
        }
        return false;
      }
      return false; // other chars are not actions here — swallow under the modal
    },
    render: () => <SettingsDialog state={s} syncTemplates={syncTemplates} />,
  };

  return mode;
}

/** Read the live templates registry + action handle from the {@link AppStoreContext}, tolerating a
 * missing provider (tests render the modal without an `<AppStoreProvider>`). Returns `null` when there
 * is no store; otherwise a `{ items, actions }` snapshot that re-renders on a `templates.items` change.
 * `useStoreWithEqualityFn` is called unconditionally (rules of hooks) — when the provider is absent the
 * subscription resolves against `EMPTY_TEMPLATES`, a stable empty snapshot, and we return `null`. */
function useLiveTemplates(): {
  items: readonly TemplateRecord[];
  actions: { remove(name: string): void; rename(oldName: string, newName: string): void };
} | null {
  const store = useContext(AppStoreContext);
  const snapshot = useStoreWithEqualityFn(
    store ?? EMPTY_STORE,
    (st: AppStore) => ({ items: st.templates.items, actions: st.actions.templates }),
    shallow,
  );
  return store === null ? null : snapshot;
}

/** A stable, frozen state snapshot for {@link EMPTY_STORE} — referentially constant so the selector's
 * `shallow` compare never sees a new ref (a fresh object each call would trip React's "getSnapshot
 * should be cached" infinite-loop guard). */
const EMPTY_STORE_STATE = {
  templates: { items: [] as readonly TemplateRecord[] },
  actions: { templates: { remove() {}, rename() {} } },
} as unknown as AppStore;

/** A stable no-op store standing in for `useStoreWithEqualityFn` when no `<AppStoreProvider>` is
 * mounted (the templates section then runs purely off the opening `current.templates`). Returns the
 * one frozen {@link EMPTY_STORE_STATE} so the subscription is referentially stable. */
const EMPTY_STORE = {
  getState: () => EMPTY_STORE_STATE,
  getInitialState: () => EMPTY_STORE_STATE,
  setState: () => {},
  subscribe: () => () => {},
} as const;

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

function SettingsDialog({
  state: s,
  syncTemplates,
}: {
  readonly state: SettingsState;
  readonly syncTemplates: (
    items: readonly TemplateRecord[],
    actions: { remove(name: string): void; rename(oldName: string, newName: string): void },
  ) => void;
}): JSX.Element {
  const theme = useTheme();
  // Design width 84, clamped to the live terminal so a narrow screen doesn't overflow the box.
  const width = useModalWidth(84);
  const kitty = useKittySupport();
  const ctrlAvailable = kitty === true;
  // Live templates registry + action handle from the app store (so `:save`/external edits track here).
  // The store is optional: tests that render the modal without an <AppStoreProvider> get `null` here
  // and the modal just runs off whatever `current.templates` it was opened with.
  const live = useLiveTemplates();
  useEffect(() => {
    if (live !== null) {
      syncTemplates(live.items, live.actions);
    }
  }, [live, syncTemplates]);
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

      {/* Read-only preview of the template body under the cursor (truncated to a few lines). */}
      {s.previewTemplate !== null && (
        <Box
          marginTop={1}
          flexShrink={0}
          flexDirection="column"
          borderStyle="round"
          borderColor={theme.muted}
          paddingX={1}
        >
          <Text color={theme.muted}>{`preview · :${s.previewTemplate.name}:`}</Text>
          <Text color={theme.text} wrap="truncate-end">
            {previewBody(s.previewTemplate.body)}
          </Text>
        </Box>
      )}

      {s.notice !== null && (
        <Box marginTop={1} flexShrink={0}>
          <Text color={theme.warning}>{s.notice}</Text>
        </Box>
      )}

      <Box marginTop={1} flexShrink={0}>
        <Text dimColor>j/k: navigate · enter: select/rename · d: delete · esc: close</Text>
      </Box>
    </Box>
  );
}

/** Flatten a template body into a single preview line (newlines → `⏎`, trimmed, capped at 200 chars
 * with an ellipsis). The `<Text wrap="truncate-end">` clamps it to the dialog width on top of this. */
function previewBody(body: string): string {
  const flat = body.replace(/\s*\n\s*/g, ' ⏎ ').trim();
  if (flat === '') {
    return '(empty)';
  }
  return flat.length > 200 ? `${flat.slice(0, 200)}…` : flat;
}

/** The maximum number of section rows shown at once — a scroll-by-cursor window so the modal shows
 * more settings at once while still avoiding an unbounded frame. */
const VISIBLE_ROWS = 22;

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
    case 'vim':
      return `vim:${row.value}`;
    case 'chatView':
      return `chatView:${row.value}`;
    case 'startupRogue':
      return `srogue:${row.field}:${row.field === 'off' ? '' : row.value}`;
    case 'collaborator':
      return `collab:${row.value ?? 'default'}`;
    case 'planner':
      return `planner:${row.value ?? 'default'}`;
    case 'crow':
      return `crow:${row.value ?? 'default'}`;
    case 'provider':
      return `provider:${row.provider}:${row.field}`;
    case 'tier':
      return `tier:${row.name}`;
    case 'role':
      return `role:${row.role}:${row.tier}`;
    case 'template':
      return `template:${row.name}`;
    case 'templateEmpty':
      return 'templateEmpty';
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
    const selected = row.value === s.persistedTheme;
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

  if (row.kind === 'vim') {
    const selected = row.value === s.vimMode;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {row.value ? 'on' : 'off'}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'chatView') {
    const selected = row.value === s.defaultChatViewMode;
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

  if (row.kind === 'startupRogue') {
    const sr = s.startupRogue;
    const color = focused ? theme.warning : theme.text;
    if (row.field === 'off') {
      const selected = sr === null;
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {selected ? '(•) ' : '( ) '}
            off (no startup rogue)
          </Text>
        </Box>
      );
    }
    if (row.field === 'harness') {
      const selected = sr !== null && sr.harness === row.value;
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {selected ? '(•) ' : '( ) '}
            {row.value}
          </Text>
        </Box>
      );
    }
    if (row.field === 'model') {
      const selected = sr !== null && sr.model === row.value;
      const label = row.value === '' ? '(default model)' : row.value;
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {selected ? '(•) ' : '( ) '}
            <Text color={theme.muted}>model · </Text>
            {label}
          </Text>
        </Box>
      );
    }
    // effort
    const selected = sr !== null && sr.effort === row.value;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {selected ? '(•) ' : '( ) '}
          <Text color={theme.muted}>effort · </Text>
          {row.value}
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

  if (row.kind === 'planner') {
    // The "(default)" row (value null) selects the no-override state; the harness rows are a radio.
    const isDefaultRow = row.value === null;
    const selected = isDefaultRow ? s.plannerHarness === null : s.plannerHarness === row.value;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    const label = isDefaultRow ? `(default) ${s.effectivePlanner}` : (row.value ?? '');
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
    const editing =
      s.editing?.kind === 'provider' &&
      s.editing.provider === row.provider &&
      s.editing.field === row.field;
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

  if (row.kind === 'templateEmpty') {
    return (
      <Box flexShrink={0}>
        <Text color={theme.muted}>
          {'  '}
          no templates — use :save &lt;name&gt; &lt;body&gt; in chat
        </Text>
      </Box>
    );
  }

  if (row.kind === 'template') {
    const renaming = s.editing?.kind === 'templateRename' && s.editing.name === row.name;
    const confirming = s.confirmingDelete === row.name;
    const color = focused ? theme.warning : theme.text;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {`:${renaming ? '' : row.name}`}
        </Text>
        {renaming ? (
          <TextInput value={s.editValue} placeholder="(name)" focused color={theme.text} />
        ) : (
          <Text color={confirming ? theme.warning : theme.muted}>
            {confirming ? '  delete? (y/n)' : ''}
          </Text>
        )}
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
