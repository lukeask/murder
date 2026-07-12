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
import { useModalHeight, useModalWidth, useTerminalSize } from '../hooks/useTerminalSize.js';
import { ACTIONS, type ActionId, type Modifier, resolveBindings } from '../input/bindings.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import {
  type BarWidgetId,
  type BarWidgetsConfig,
  resolveBarWidgetConfig,
} from '../selectors/barWidgetRegistry.js';
import { harnessLabel, harnessShortLabel } from '../selectors/harnessDisplay.js';
import type {
  LlmEnvWire,
  LlmModelOverrideWire,
  LlmProviderId,
  LlmWire,
  SettingsActions,
  StartupRogueModelWire,
  StartupRogueWire,
} from '../store/settings/settingsActions.js';
import type { DefaultChatViewMode } from '../store/settings/settingsSlice.js';
import type { AppStore } from '../store/store.js';
import type { TemplateRecord } from '../store/templates/templatesSlice.js';
import type { ThemeRecord } from '../store/themes/themesSlice.js';
import { capsStore, type KittySupport, useKittySupport } from '../terminal/capsStore.js';
import { DEFAULT_THEME_ID, listThemeRecords, type ThemeId } from '../theme/palettes.js';
import { setTheme, useTheme } from '../theme/themeStore.js';
import {
  buildCategoryRows,
  categoryIndexById,
  SETTINGS_CATEGORIES,
} from './settings/categories.js';
import {
  defaultEffortFor,
  defaultModelFor,
  HARNESSES,
  startupRogueEffortsFor,
} from './settings/items/harnesses.js';
import { REBINDABLE, RESERVED_KEYS } from './settings/items/keybindings.js';
import { ENV_PROVIDERS, mergedTiers, tierNames } from './settings/items/llm.js';
import { TEMPLATE_NAME_RE } from './settings/items/templates.js';
import type { SettingsCategoryId, SettingsRow } from './settings/types.js';
import { deleteLastChar, insertChar, TextInput } from './TextInput.js';

// Bring the dispatcher's `onUncaptured` augmentation into scope (the printable/capture router needs it).
import '../input/dispatcher.js';

/** Intent union — structural-key actions only. Printable chars (j/k + the captured rebind key) ride
 * `onUncaptured`, not the keymap. */
type SettingsIntent =
  | 'cursorUp'
  | 'cursorDown'
  | 'enterPane'
  | 'exitPane'
  | 'confirm'
  | 'cancel'
  | 'backspace'
  | 'deleteAll';

const BUILTIN_POLICY_TEMPLATES: Readonly<Record<string, readonly { readonly selectors: readonly unknown[] }[]>> = {
  'local-then-free': [
    { selectors: [{ match: { locality: 'local' } }] },
    { selectors: [{ match: { locality: 'remote', cost_class: 'free' } }] },
  ],
  'remote-free': [{ selectors: [{ match: { locality: 'remote', cost_class: 'free' } }] }],
  'local-only': [{ selectors: [{ match: { locality: 'local' } }] }],
};

/** Options for the settings mode factory. */
export interface SettingsModeOptions {
  /** Called when the modal is dismissed (after the mode exits). */
  readonly onDismiss?: () => void;
}

/** The stable mode id for idempotent re-enter. */
export const SETTINGS_MODE_ID = 'settings';

/** Whether a row can hold the cursor (headers + read-only tier rows are skipped). A modifier row is
 * selectable even when disabled — the cursor can rest on it (to show the notice), but `confirm` is a
 * no-op there. */
function isSelectable(row: SettingsRow): boolean {
  return row.kind !== 'header' && row.kind !== 'tier' && row.kind !== 'templateEmpty';
}

/** A text-entry target — either a provider field (api_key / local base_url) or a template rename. */
type EditTarget =
  | {
      readonly kind: 'provider';
      readonly provider: LlmProviderId;
      readonly field: 'api_key' | 'base_url';
    }
  | { readonly kind: 'templateRename'; readonly name: string }
  | { readonly kind: 'templateCreateName' }
  | { readonly kind: 'templateCreateBody'; readonly name: string }
  | { readonly kind: 'themeImport' };

type LlmForm =
  | {
      readonly kind: 'provider';
      readonly providerType: string;
      readonly providerId: string | null;
      readonly builtin: boolean;
      name: string;
      endpoint: string;
      apiKey: string;
      source: 'recommended' | 'discovered' | 'custom';
      include: string;
      exclude: string;
      overrides: string;
      field: number;
    }
  | {
      readonly kind: 'policy';
      readonly policyId: string | null;
      name: string;
      groups: string;
      field: number;
    };

/** Mutable closure state — not React state. Mutated by `onIntent`/`onUncaptured`; `render` reads it. */
interface SettingsState {
  /** The live row list — rebuilt whenever the draft `llm` changes (tier/role rows are dynamic). */
  rows: readonly SettingsRow[];
  /** Which pane owns j/k/up/down right now. */
  activePane: 'categories' | 'settings' | 'editor';
  /** The category selected in the sidebar. */
  categoryId: SettingsCategoryId;
  /** The sidebar cursor index. */
  categoryCursor: number;
  /** One row cursor per category id, so returning to a category restores its focused setting. */
  rowCursors: Partial<Record<SettingsCategoryId, number>>;
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
  /** The draft workspace count (committed via `update` on selection). */
  workspaceCount: number;
  /** Workspace count at modal open — used to detect the 1 → >1 transition. */
  initialWorkspaceCount: number;
  /** Show the Kitty ctrl+shift workspace-mapping notice after enabling multi-workspace. */
  showKittyWorkspaceMappingWarning: boolean;
  /** The draft vim-mode flag (committed via `update` on selection). */
  vimMode: boolean;
  /** Draft bar-widget overrides (partial map; defaults resolved via the registry). */
  barWidgets: BarWidgetsConfig;
  /** The draft default chat view mode (TUIchat-3; committed via `update` on selection). Only
   * verbose/condensed are settable — tmux is reachable only via the per-pane cycle key. */
  defaultChatViewMode: DefaultChatViewMode;
  /** The draft Startup Rogue (`null` = off). Drives the model/effort sub-rows + persisted on change. */
  startupRogue: StartupRogueWire | null;
  /** Startup Rogue model choices by harness. */
  startupRogueModels: Readonly<Record<string, readonly StartupRogueModelWire[]>>;
  /** Startup Rogue effort choices by harness. */
  startupRogueEfforts: Readonly<Record<string, readonly string[]>>;
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
  /** Open LLM provider/policy form. The form owns its draft until Save. */
  llmForm: LlmForm | null;
  /** A text field inside the open LLM form is receiving printable input. */
  llmFormEditing: boolean;
  /** The action currently capturing its next-key rebind, or `null` when not capturing. */
  capturing: ActionId | null;
  /** The live saved templates (browse/preview/rename/delete source). Synced from the app store. */
  templates: readonly TemplateRecord[];
  /** The templates action handle (rename/remove). Synced from the app store. */
  templateActions: {
    remove(name: string): void;
    rename(oldName: string, newName: string): void;
    save(name: string, body: string): void;
  };
  /** The template whose body is previewed under the cursor, or `null` when the cursor is elsewhere. */
  previewTemplate: TemplateRecord | null;
  /** The template name pending a delete confirm ("(y/n)"), or `null` when not confirming. */
  confirmingDelete: string | null;
  /** The custom theme id pending delete confirm, or `null`. */
  confirmingThemeDelete: string | null;
  /** The live saved themes (theme picker source). Synced from the app store. */
  themes: readonly ThemeRecord[];
  /** Theme registry actions (import/remove). Synced from the app store. */
  themeActions: {
    importTheme(json: string): Promise<string>;
    remove(id: string): Promise<void>;
  };
  /** The transient inline notice (rejection reason / hint), or `null`. */
  notice: string | null;
}

/** Find the first selectable row index at or after `from` (wrapping). Used to seed the cursor and to
 * skip header/read-only rows during navigation. */
function firstSelectableFrom(rows: readonly SettingsRow[], from: number): number {
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
 * the row reflects an in-session rebind/modifier change before it is even saved). `ctrlAvailable`
 * matters because `resolveBindings('ctrl', false, ...)` intentionally degrades labels to Alt. */
function bindingLabel(
  action: ActionId,
  modifier: Modifier,
  ctrlAvailable: boolean,
  overrides: Record<string, string>,
): string {
  return resolveBindings(
    modifier,
    ctrlAvailable,
    overrides as Partial<Record<ActionId, string>>,
  ).label(
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
    readonly workspaceCount?: number;
    readonly vimMode?: boolean;
    readonly barWidgets?: BarWidgetsConfig;
    readonly defaultChatViewMode?: DefaultChatViewMode;
    readonly startupRogue?: StartupRogueWire | null;
    readonly startupRogueModels?: Readonly<Record<string, readonly StartupRogueModelWire[]>>;
    readonly startupRogueEfforts?: Readonly<Record<string, readonly string[]>>;
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
      save(name: string, body: string): void;
    };
    readonly themes?: readonly ThemeRecord[];
    readonly themeActions?: {
      importTheme(json: string): Promise<string>;
      remove(id: string): Promise<void>;
    };
  },
  opts: SettingsModeOptions = {},
): Mode<SettingsIntent> {
  const id = SETTINGS_MODE_ID;

  const initialLlm: LlmWire = current.llm ?? {};
  const initialStartupRogue: StartupRogueWire | null = current.startupRogue ?? null;
  const initialStartupRogueModels = current.startupRogueModels ?? {};
  const initialStartupRogueEfforts = current.startupRogueEfforts ?? {};
  const initialTemplates: readonly TemplateRecord[] = current.templates ?? [];
  const initialThemes: readonly ThemeRecord[] = current.themes ?? listThemeRecords();
  const initialCategoryId: SettingsCategoryId = 'appearance';
  const initialRows = buildCategoryRows(initialCategoryId, {
    llm: initialLlm,
    startupRogue: initialStartupRogue,
    startupRogueModels: initialStartupRogueModels,
    startupRogueEfforts: initialStartupRogueEfforts,
    templates: initialTemplates,
    themes: initialThemes,
    barWidgets: current.barWidgets ?? {},
  });
  const initialCursor = firstSelectableFrom(initialRows, 0);
  const s: SettingsState = {
    rows: initialRows,
    activePane: 'categories',
    categoryId: initialCategoryId,
    categoryCursor: categoryIndexById(initialCategoryId),
    rowCursors: { [initialCategoryId]: initialCursor },
    cursor: initialCursor,
    modifier: current.modifier,
    persistedTheme: current.theme,
    theme: current.theme,
    paneGap: current.paneGap,
    workspaceCount: current.workspaceCount ?? 1,
    initialWorkspaceCount: current.workspaceCount ?? 1,
    showKittyWorkspaceMappingWarning: false,
    vimMode: current.vimMode ?? false,
    barWidgets: { ...(current.barWidgets ?? {}) },
    defaultChatViewMode: current.defaultChatViewMode ?? 'verbose',
    startupRogue: initialStartupRogue,
    startupRogueModels: initialStartupRogueModels,
    startupRogueEfforts: initialStartupRogueEfforts,
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
    llmForm: null,
    llmFormEditing: false,
    capturing: null,
    templates: initialTemplates,
    templateActions: current.templateActions ?? { remove() {}, rename() {}, save() {} },
    previewTemplate: null,
    confirmingDelete: null,
    confirmingThemeDelete: null,
    themes: initialThemes,
    themeActions: current.themeActions ?? {
      async importTheme() {
        return '';
      },
      async remove() {},
    },
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

  function modalIsBusy(): boolean {
    return (
      s.capturing !== null ||
      s.editing !== null ||
      s.llmFormEditing ||
      s.confirmingDelete !== null ||
      s.confirmingThemeDelete !== null
    );
  }

  function buildRowsFor(categoryId: SettingsCategoryId): readonly SettingsRow[] {
    return buildCategoryRows(categoryId, {
      llm: s.llm,
      startupRogue: s.startupRogue,
      startupRogueModels: s.startupRogueModels,
      startupRogueEfforts: s.startupRogueEfforts,
      templates: s.templates,
      themes: s.themes,
      barWidgets: s.barWidgets,
    });
  }

  function switchCategory(delta: number): void {
    if (s.activePane !== 'categories' || modalIsBusy()) {
      return;
    }
    const len = SETTINGS_CATEGORIES.length;
    const nextCursor = (s.categoryCursor + delta + len) % len;
    const category = SETTINGS_CATEGORIES[nextCursor];
    if (category === undefined) {
      return;
    }
    const prev = s.rows[s.cursor];
    if (prev?.kind === 'theme') {
      restoreThemePreview();
    }
    s.categoryCursor = nextCursor;
    s.categoryId = category.id;
    s.rows = buildRowsFor(category.id);
    const savedCursor = s.rowCursors[category.id] ?? 0;
    s.cursor = firstSelectableFrom(s.rows, Math.min(savedCursor, s.rows.length - 1));
    s.previewTemplate = null;
    s.notice = null;
    refresh();
  }

  function enterSettingsPane(): void {
    if (modalIsBusy()) {
      return;
    }
    if (s.activePane === 'settings' && s.categoryId === 'llm') {
      const row = s.rows[s.cursor];
      if (row?.kind === 'llmProvider' || row?.kind === 'llmPolicy' || row?.kind === 'llmAddProvider' || row?.kind === 'llmCreatePolicy') {
        confirm();
        return;
      }
    }
    s.activePane = 'settings';
    s.notice = null;
    refresh();
  }

  function enterCategoryPane(): void {
    if (modalIsBusy()) {
      return;
    }
    if (s.activePane === 'editor') {
      s.activePane = 'settings';
      s.notice = null;
      refresh();
      return;
    }
    const prev = s.rows[s.cursor];
    if (prev?.kind === 'theme') {
      restoreThemePreview();
    }
    s.activePane = 'categories';
    s.previewTemplate = null;
    s.notice = null;
    refresh();
  }

  /** Move the cursor by `delta`, skipping header/read-only rows (wrapping). */
  function moveCursor(delta: number): void {
    if (s.activePane === 'editor') {
      moveFormField(delta);
      return;
    }
    if (s.activePane !== 'settings') {
      switchCategory(delta);
      return;
    }
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
        s.rowCursors = { ...s.rowCursors, [s.categoryId]: s.cursor };
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

  /** Commit the draft workspace count (optimistic update). */
  function selectWorkspaceCount(value: number): void {
    s.workspaceCount = value;
    if (s.initialWorkspaceCount === 1 && value > 1 && s.modifier === 'ctrl') {
      s.showKittyWorkspaceMappingWarning = true;
    } else if (value === 1) {
      s.showKittyWorkspaceMappingWarning = false;
    }
    void actions.update({ workspace_count: value });
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

  function patchBarWidget(
    widgetId: BarWidgetId,
    patch: {
      enabled?: boolean;
      placement?: 'top' | 'bottom';
      adaptive?: boolean;
      harnesses?: readonly string[];
    },
  ): void {
    const current = resolveBarWidgetConfig(widgetId, s.barWidgets);
    const merged = {
      enabled: current.enabled,
      placement: current.placement,
      adaptive: current.adaptive,
      ...(current.harnesses !== undefined ? { harnesses: [...current.harnesses] } : {}),
      ...patch,
    };
    s.barWidgets = { ...s.barWidgets, [widgetId]: merged };
    void actions.update({ bar_widgets: { [widgetId]: merged } });
    s.notice = null;
    refresh();
  }

  function toggleUsageBarHarness(harness: string): void {
    const config = resolveBarWidgetConfig('usage', s.barWidgets);
    const all = [...HARNESSES];
    const current =
      config.harnesses === undefined || config.harnesses.length === 0 ? all : [...config.harnesses];
    const checked = current.includes(harness);
    const next = checked ? current.filter((h) => h !== harness) : [...current, harness];
    const normalized =
      next.length === 0 || next.length === all.length
        ? []
        : next.sort((a, b) => all.indexOf(a) - all.indexOf(b));
    patchBarWidget('usage', { harnesses: normalized });
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
    const efforts = startupRogueEffortsFor(harness, s.startupRogueEfforts);
    const next: StartupRogueWire = {
      harness,
      model: same ? prev.model : defaultModelFor(harness, s.startupRogueModels),
      effort:
        same && prev.effort !== null && efforts.includes(prev.effort)
          ? prev.effort
          : defaultEffortFor(harness, s.startupRogueEfforts),
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

  function toggleLlmFunctionality(): void {
    const disabled = !s.llm.disabled;
    s.llm = { ...s.llm, disabled };
    rebuildRows();
    void actions.llm.setDisabled(disabled);
    refresh();
  }

  function toggleLlmProvider(providerId: string): void {
    const existing = s.llm.providers?.[providerId];
    const enabled = !(existing?.enabled ?? false);
    s.llm = {
      ...s.llm,
      providers: {
        ...(s.llm.providers ?? {}),
        [providerId]: { ...(existing ?? {}), enabled },
      },
    };
    rebuildRows();
    void actions.llm.updateProvider(providerId, { enabled });
    refresh();
  }

  function activateLlmPolicy(policyId: string): void {
    s.llm = { ...s.llm, active_policy: policyId };
    rebuildRows();
    void actions.llm.activatePolicy(policyId);
    refresh();
  }

  function cloneBuiltinPolicy(policyId: string): void {
    const template = BUILTIN_POLICY_TEMPLATES[policyId];
    const name = `${policyId.replaceAll('-', ' ')} copy`;
    if (template === undefined) return;
    void actions.llm.createPolicy(name, { groups: template }).then((id) => {
      if (id !== null) {
        s.llm = { ...s.llm, policies: { ...(s.llm.policies ?? {}), [id]: { builtin: false, name, groups: template } } };
        rebuildRows();
        s.notice = `Cloned as ${name}`;
        refresh();
      }
    });
  }

  function selectFeaturePolicy(feature: string): void {
    const choices = ['local-then-free', 'remote-free', 'local-only', 'oracle-smart', ...Object.keys(s.llm.policies ?? {})];
    const current = s.llm.feature_policies?.[feature] ?? s.llm.active_policy ?? 'local-then-free';
    const next = choices[(choices.indexOf(current) + 1) % choices.length] ?? 'local-then-free';
    s.llm = { ...s.llm, feature_policies: { ...(s.llm.feature_policies ?? {}), [feature]: next } };
    rebuildRows();
    void actions.update({ llm: { feature_policies: { [feature]: next } } });
    refresh();
  }

  function openProviderForm(
    providerType: string,
    providerId: string | null,
    builtin: boolean,
  ): void {
    const provider = providerId === null ? undefined : s.llm.providers?.[providerId];
    s.llmForm = {
      kind: 'provider',
      providerType,
      providerId,
      builtin,
      name: provider?.name ?? (providerId?.replaceAll('-', ' ') ?? ''),
      endpoint: provider?.endpoint ?? '',
      apiKey: provider?.auth?.api_key ?? provider?.api_key ?? '',
      source: provider?.models?.source ?? 'recommended',
      include: (provider?.models?.include ?? []).join(', '),
      exclude: (provider?.models?.exclude ?? []).join(', '),
      overrides: JSON.stringify(provider?.models?.overrides ?? {}),
      field: 0,
    };
    s.llmFormEditing = false;
    s.activePane = 'editor';
    s.notice = null;
    refresh();
  }

  function openPolicyForm(policyId: string | null): void {
    const policy = policyId === null ? undefined : s.llm.policies?.[policyId];
    s.llmForm = {
      kind: 'policy',
      policyId,
      name: policy?.name ?? '',
      groups: JSON.stringify(policy?.groups ?? [], null, 0),
      field: 0,
    };
    s.llmFormEditing = false;
    s.activePane = 'editor';
    s.notice = null;
    refresh();
  }

  function formFieldCount(): number {
    if (s.llmForm?.kind === 'provider') return 9; // basic fields, catalog controls, save, cancel
    if (s.llmForm?.kind === 'policy') return 4; // name, groups, save, cancel
    return 0;
  }

  function moveFormField(delta: number): void {
    if (s.llmForm === null || s.llmFormEditing) return;
    const count = formFieldCount();
    s.llmForm.field = (s.llmForm.field + delta + count) % count;
    refresh();
  }

  function beginFormTextEdit(): void {
    const form = s.llmForm;
    if (form === null) return;
    const value = form.kind === 'provider'
      ? form.field === 0 ? form.name : form.field === 1 ? form.endpoint : form.field === 2 ? form.apiKey : form.field === 4 ? form.include : form.field === 5 ? form.exclude : form.overrides
      : form.field === 0 ? form.name : form.groups;
    s.editValue = value;
    s.llmFormEditing = true;
    s.notice = 'Type value. Enter to apply, Esc to cancel.';
    refresh();
  }

  function commitFormTextEdit(): void {
    const form = s.llmForm;
    if (form === null) return;
    if (form.kind === 'provider') {
      if (form.field === 0) form.name = s.editValue;
      else if (form.field === 1) form.endpoint = s.editValue;
      else if (form.field === 2) form.apiKey = s.editValue;
      else if (form.field === 4) form.include = s.editValue;
      else if (form.field === 5) form.exclude = s.editValue;
      else if (form.field === 6) form.overrides = s.editValue;
    } else if (form.field === 0) {
      form.name = s.editValue;
    } else if (form.field === 1) {
      form.groups = s.editValue;
    }
    s.editValue = '';
    s.llmFormEditing = false;
    s.notice = null;
    refresh();
  }

  function cancelLlmForm(): void {
    s.llmForm = null;
    s.llmFormEditing = false;
    s.editValue = '';
    s.activePane = 'settings';
    s.notice = null;
    refresh();
  }

  function saveLlmForm(): void {
    const form = s.llmForm;
    if (form === null) return;
    if (form.kind === 'provider') {
      if (form.name.trim() === '' || (form.providerId === null && form.endpoint.trim() === '')) {
        s.notice = 'Provider name and endpoint are required';
        refresh();
        return;
      }
      let overrides: Record<string, LlmModelOverrideWire>;
      try {
        const parsed: unknown = JSON.parse(form.overrides || '{}');
        if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
        overrides = parsed as Record<string, LlmModelOverrideWire>;
      } catch {
        s.notice = 'Model overrides must be a JSON object';
        refresh();
        return;
      }
      const models = {
        source: form.source,
        include: parseModelList(form.include),
        exclude: parseModelList(form.exclude),
        overrides,
      };
      const patch = {
        name: form.name.trim(), endpoint: form.endpoint.trim(),
        auth: { api_key: form.apiKey }, models,
      };
      if (form.providerId === null) {
        if (form.providerType !== 'openai_compatible' && form.providerType !== 'lemonade') return;
        void actions.llm.createProvider({ type: form.providerType, enabled: false, ...patch }).then((id) => {
          if (id !== null) {
            s.llm = { ...s.llm, providers: { ...(s.llm.providers ?? {}), [id]: { type: form.providerType, enabled: false, ...patch } } };
            rebuildRows();
            cancelLlmForm();
          }
        });
      } else {
        void actions.llm.updateProvider(form.providerId, patch);
        s.llm = { ...s.llm, providers: { ...(s.llm.providers ?? {}), [form.providerId]: { ...(s.llm.providers?.[form.providerId] ?? {}), ...patch } } };
        rebuildRows();
        cancelLlmForm();
      }
      return;
    }
    let groups: unknown;
    try {
      groups = JSON.parse(form.groups);
      if (!Array.isArray(groups)) throw new Error('groups must be an array');
    } catch {
      s.notice = 'Groups must be valid JSON array';
      refresh();
      return;
    }
    if (form.name.trim() === '') {
      s.notice = 'Policy name is required';
      refresh();
      return;
    }
    if (form.policyId === null) {
      void actions.llm.createPolicy(form.name.trim(), { groups }).then((id) => {
        if (id !== null) {
          s.llm = { ...s.llm, policies: { ...(s.llm.policies ?? {}), [id]: { builtin: false, name: form.name.trim(), groups: groups as [] } } };
          rebuildRows();
          cancelLlmForm();
        }
      });
    } else {
      void actions.llm.updatePolicy(form.policyId, { name: form.name.trim(), groups });
      s.llm = { ...s.llm, policies: { ...(s.llm.policies ?? {}), [form.policyId]: { builtin: false, name: form.name.trim(), groups: groups as [] } } };
      rebuildRows();
      cancelLlmForm();
    }
  }

  function deleteLlmFormTarget(): void {
    const form = s.llmForm;
    if (form?.kind === 'provider' && form.providerId !== null && !form.builtin) {
      void actions.llm.deleteProvider(form.providerId);
      s.llm = { ...s.llm, providers: Object.fromEntries(Object.entries(s.llm.providers ?? {}).filter(([id]) => id !== form.providerId)) };
      rebuildRows();
      cancelLlmForm();
    } else if (form?.kind === 'policy' && form.policyId !== null) {
      const references = Object.entries(s.llm.feature_policies ?? {})
        .filter(([, policyId]) => policyId === form.policyId)
        .map(([feature]) => feature.replaceAll('_', ' '));
      if (references.length > 0) {
        s.notice = `Policy is used by: ${references.join(', ')}`;
        refresh();
        return;
      }
      void actions.llm.deletePolicy(form.policyId);
      s.llm = { ...s.llm, policies: Object.fromEntries(Object.entries(s.llm.policies ?? {}).filter(([id]) => id !== form.policyId)) };
      rebuildRows();
      cancelLlmForm();
    }
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
    if (s.editing.kind === 'templateCreateName') {
      commitTemplateCreateName();
      return;
    }
    if (s.editing.kind === 'templateCreateBody') {
      commitTemplateCreateBody();
      return;
    }
    if (s.editing.kind === 'themeImport') {
      void commitThemeImport();
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

  function validateTemplateName(name: string, originalName: string | null): string | null {
    if (name === '') {
      return 'Template name cannot be empty';
    }
    if (!TEMPLATE_NAME_RE.test(name)) {
      return `"${name}" is invalid — use letters, digits, _ or - only`;
    }
    if (name !== originalName && s.templates.some((t) => t.name === name)) {
      return `A template named "${name}" already exists`;
    }
    return null;
  }

  function beginTemplateCreate(): void {
    s.editing = { kind: 'templateCreateName' };
    s.editValue = '';
    s.previewTemplate = null;
    s.notice = 'New template name. Enter to continue, Esc to cancel.';
    refresh();
  }

  function commitTemplateCreateName(): void {
    if (s.editing === null || s.editing.kind !== 'templateCreateName') {
      return;
    }
    const name = s.editValue.trim();
    const error = validateTemplateName(name, null);
    if (error !== null) {
      s.notice = error;
      refresh();
      return;
    }
    s.editing = { kind: 'templateCreateBody', name };
    s.editValue = '';
    s.notice = 'Template body. Enter to save, Esc to cancel.';
    refresh();
  }

  function commitTemplateCreateBody(): void {
    if (s.editing === null || s.editing.kind !== 'templateCreateBody') {
      return;
    }
    const name = s.editing.name;
    const body = s.editValue;
    s.templateActions.save(name, body);
    s.editing = null;
    s.editValue = '';
    s.notice = null;
    rebuildRows();
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
    const error = validateTemplateName(newName, oldName);
    if (error !== null) {
      s.notice = error;
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

  function beginThemeDelete(id: string): void {
    s.confirmingThemeDelete = id;
    s.notice = `delete theme "${id}"? (y/n)`;
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

  async function resolveThemeDelete(confirmed: boolean): Promise<void> {
    const id = s.confirmingThemeDelete;
    s.confirmingThemeDelete = null;
    s.notice = null;
    if (confirmed && id !== null) {
      await s.themeActions.remove(id);
      if (s.theme === id || s.persistedTheme === id) {
        s.theme = DEFAULT_THEME_ID;
        s.persistedTheme = DEFAULT_THEME_ID;
        setTheme(DEFAULT_THEME_ID);
        void actions.update({ theme: DEFAULT_THEME_ID });
      }
      rebuildRows();
    }
    refresh();
  }

  function beginThemeImport(): void {
    s.editing = { kind: 'themeImport' };
    s.editValue = '';
    s.notice = 'Paste theme JSON. Enter to import, Esc to cancel.';
    refresh();
  }

  async function commitThemeImport(): Promise<void> {
    if (s.editing === null || s.editing.kind !== 'themeImport') {
      return;
    }
    const json = s.editValue.trim();
    if (json === '') {
      s.notice = 'Theme JSON cannot be empty';
      refresh();
      return;
    }
    try {
      const newId = await s.themeActions.importTheme(json);
      s.editing = null;
      s.editValue = '';
      s.theme = newId;
      setTheme(newId);
      s.notice = `Imported theme "${newId}" — Enter on the row to persist selection`;
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      s.notice = message;
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
      save(name: string, body: string): void;
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

  function syncThemes(
    items: readonly ThemeRecord[],
    themeActions: {
      importTheme(json: string): Promise<string>;
      remove(id: string): Promise<void>;
    },
  ): void {
    s.themeActions = themeActions;
    const changed =
      items.length !== s.themes.length ||
      items.some(
        (t, i) =>
          t.id !== s.themes[i]?.id ||
          t.name !== s.themes[i]?.name ||
          t.builtin !== s.themes[i]?.builtin,
      );
    if (!changed) {
      return;
    }
    s.themes = items;
    rebuildRows();
    refresh();
  }

  /** Rebuild the live row list from the draft llm, clamping the cursor onto a still-selectable row. */
  function rebuildRows(): void {
    s.rows = buildRowsFor(s.categoryId);
    if (s.cursor >= s.rows.length || !isSelectable(s.rows[s.cursor] as SettingsRow)) {
      s.cursor = firstSelectableFrom(s.rows, Math.min(s.cursor, s.rows.length - 1));
    }
    s.rowCursors = { ...s.rowCursors, [s.categoryId]: s.cursor };
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
    if (s.activePane === 'categories') {
      enterSettingsPane();
      return;
    }
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
      case 'workspaceCount':
        selectWorkspaceCount(row.value);
        break;
      case 'vim':
        selectVim(row.value);
        break;
      case 'barWidget':
        if (row.field === 'enabled') {
          patchBarWidget(row.widgetId, { enabled: row.value });
        } else if (row.field === 'adaptive') {
          patchBarWidget(row.widgetId, { adaptive: row.value });
        } else if (row.field === 'placement') {
          patchBarWidget(row.widgetId, { placement: row.value });
        }
        break;
      case 'barWidgetHarness':
        toggleUsageBarHarness(row.value);
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
      case 'llmGlobal':
        s.notice = 'Space toggles direct LLM functionality.';
        refresh();
        break;
      case 'llmProvider':
        openProviderForm(
          s.llm.providers?.[row.providerId]?.type ?? row.providerId,
          row.providerId,
          row.builtin,
        );
        break;
      case 'llmPolicy':
        if (row.builtin) {
          cloneBuiltinPolicy(row.policyId);
        } else {
          openPolicyForm(row.policyId);
        }
        break;
      case 'llmAddProvider':
        openProviderForm(row.providerType, null, false);
        break;
      case 'llmCreatePolicy':
        openPolicyForm(null);
        break;
      case 'llmFeaturePolicy':
        selectFeaturePolicy(row.feature);
        break;
      case 'role':
        selectRole(row.role, row.tier);
        break;
      case 'templateCreate':
        beginTemplateCreate();
        break;
      case 'template':
        beginTemplateRename(row.name);
        break;
      case 'themeImport':
        beginThemeImport();
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
    if (s.llmFormEditing) {
      s.llmFormEditing = false;
      s.editValue = '';
      s.notice = null;
      refresh();
      return;
    }
    if (s.llmForm !== null) {
      cancelLlmForm();
      return;
    }
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
    if (s.confirmingThemeDelete !== null) {
      void resolveThemeDelete(false);
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
      { chord: { key: { rightArrow: true } }, intent: 'enterPane', description: 'settings' },
      { chord: { key: { leftArrow: true } }, intent: 'exitPane', description: 'categories' },
      { chord: { key: { return: true } }, intent: 'confirm', description: 'select' },
      { chord: { key: { escape: true } }, intent: 'cancel', description: 'close' },
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      { chord: { input: 'u', key: { meta: true } }, intent: 'deleteAll', description: 'clear' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'cursorUp':
          // Cursor moves are inert while capturing, editing, or awaiting a delete confirm.
          if (!modalIsBusy()) {
            moveCursor(-1);
          }
          break;
        case 'cursorDown':
          if (!modalIsBusy()) {
            moveCursor(1);
          }
          break;
        case 'enterPane':
          enterSettingsPane();
          break;
        case 'exitPane':
          enterCategoryPane();
          break;
        case 'confirm':
          // Enter commits an in-progress text edit; otherwise acts on the focused row. Inert while a
          // delete confirm is pending (it only accepts y/n via onUncaptured, or Esc to cancel).
          if (s.llmFormEditing) {
            commitFormTextEdit();
          } else if (s.llmForm !== null) {
            const form = s.llmForm;
            const textField = (form.kind === 'provider' && (form.field <= 2 || (form.field >= 4 && form.field <= 6))) || (form.kind === 'policy' && form.field <= 1);
            if (textField) {
              beginFormTextEdit();
            } else if ((form.kind === 'provider' && form.field === 3)) {
              const sources: Array<'recommended' | 'discovered' | 'custom'> = ['recommended', 'discovered', 'custom'];
              form.source = sources[(sources.indexOf(form.source) + 1) % sources.length] ?? 'recommended';
              refresh();
            } else if (form.field === (form.kind === 'provider' ? 7 : 2)) {
              saveLlmForm();
            } else {
              cancelLlmForm();
            }
          } else if (s.editing !== null) {
            commitEdit();
          } else if (
            s.capturing === null &&
            s.confirmingDelete === null &&
            s.confirmingThemeDelete === null
          ) {
            confirm();
          }
          break;
        case 'cancel':
          dismiss();
          break;
        case 'backspace':
          if (s.llmFormEditing) {
            s.editValue = deleteLastChar(s.editValue);
            refresh();
          } else if (s.editing !== null) {
            s.editValue = deleteLastChar(s.editValue);
            refresh();
          }
          break;
        case 'deleteAll':
          if (s.llmFormEditing) {
            s.editValue = '';
            refresh();
          } else if (s.editing !== null) {
            s.editValue = '';
            refresh();
          }
          break;
        default:
          return intent satisfies never;
      }
    },
    onUncaptured(input: string, key: Key): boolean {
      if (s.llmFormEditing) {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) return false;
        s.editValue = insertChar(s.editValue, input);
        refresh();
        return true;
      }
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
      if (s.confirmingThemeDelete !== null) {
        if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
          return false;
        }
        if (input === 'y' || input === 'Y') {
          void resolveThemeDelete(true);
        } else if (input === 'n' || input === 'N') {
          void resolveThemeDelete(false);
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
      if (input === 'l') {
        enterSettingsPane();
        return true;
      }
      if (input === 'h') {
        enterCategoryPane();
        return true;
      }
      if (input === ' ') {
        const row = s.rows[s.cursor];
        if (row?.kind === 'llmGlobal') toggleLlmFunctionality();
        else if (row?.kind === 'llmProvider') toggleLlmProvider(row.providerId);
        else if (row?.kind === 'llmPolicy') activateLlmPolicy(row.policyId);
        else return false;
        return true;
      }
      if (input === 'd' && s.activePane === 'editor' && s.llmForm !== null) {
        deleteLlmFormTarget();
        return true;
      }
      // `d` on a template row opens a delete confirm (an unobtrusive key; only acts on a template).
      if (input === 'd') {
        const row = s.rows[s.cursor];
        if (row?.kind === 'template') {
          beginTemplateDelete(row.name);
          return true;
        }
        if (row?.kind === 'theme' && !row.builtin) {
          beginThemeDelete(row.value);
          return true;
        }
        return false;
      }
      return false; // other chars are not actions here — swallow under the modal
    },
    render: () => (
      <SettingsDialog state={s} syncTemplates={syncTemplates} syncThemes={syncThemes} />
    ),
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
  actions: {
    remove(name: string): void;
    rename(oldName: string, newName: string): void;
    save(name: string, body: string): void;
  };
} | null {
  const store = useContext(AppStoreContext);
  const snapshot = useStoreWithEqualityFn(
    store ?? EMPTY_STORE,
    (st: AppStore) => ({ items: st.templates.items, actions: st.actions.templates }),
    shallow,
  );
  return store === null ? null : snapshot;
}

function useLiveThemes(): {
  items: readonly ThemeRecord[];
  actions: {
    importTheme(json: string): Promise<string>;
    remove(id: string): Promise<void>;
  };
} | null {
  const store = useContext(AppStoreContext);
  const snapshot = useStoreWithEqualityFn(
    store ?? EMPTY_STORE,
    (st: AppStore) => ({ items: st.themes.items, actions: st.actions.themes }),
    shallow,
  );
  return store === null ? null : snapshot;
}

/** A stable, frozen state snapshot for {@link EMPTY_STORE} — referentially constant so the selector's
 * `shallow` compare never sees a new ref (a fresh object each call would trip React's "getSnapshot
 * should be cached" infinite-loop guard). */
const EMPTY_STORE_STATE = {
  templates: { items: [] as readonly TemplateRecord[] },
  themes: { items: [] as readonly ThemeRecord[] },
  actions: {
    templates: { remove() {}, rename() {}, save() {} },
    themes: {
      async importTheme() {
        return '';
      },
      async remove() {},
      async load() {},
      async save() {},
    },
  },
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

const KITTY_WORKSPACE_MAPPING_WARNING =
  'Kitty users with ctrl as the command modifier must add these conditional mappings: ' +
  'map --when-focus-on var:murder_tui=1 ctrl+shift+k no_op and ' +
  'map --when-focus-on var:murder_tui=1 ctrl+shift+j no_op.';

// ---------------------------------------------------------------------------------------------
// Presentation — pure functions of state (rule 1). Reads the live caps/theme stores via hooks.
// ---------------------------------------------------------------------------------------------

function SettingsDialog({
  state: s,
  syncTemplates,
  syncThemes,
}: {
  readonly state: SettingsState;
  readonly syncTemplates: (
    items: readonly TemplateRecord[],
    actions: {
      remove(name: string): void;
      rename(oldName: string, newName: string): void;
      save(name: string, body: string): void;
    },
  ) => void;
  readonly syncThemes: (
    items: readonly ThemeRecord[],
    actions: {
      importTheme(json: string): Promise<string>;
      remove(id: string): Promise<void>;
    },
  ) => void;
}): JSX.Element {
  const theme = useTheme();
  // Design width 84, clamped to the live terminal so a narrow screen doesn't overflow the box.
  const width = useModalWidth(84);
  const height = useModalHeight(0.8);
  const { rows: termRows } = useTerminalSize();
  const kitty = useKittySupport();
  const ctrlAvailable = kitty === true;
  // Live templates registry + action handle from the app store (so `:save`/external edits track here).
  // The store is optional: tests that render the modal without an <AppStoreProvider> get `null` here
  // and the modal just runs off whatever `current.templates` it was opened with.
  const live = useLiveTemplates();
  const liveThemes = useLiveThemes();
  useEffect(() => {
    if (live !== null) {
      syncTemplates(live.items, live.actions);
    }
  }, [live, syncTemplates]);
  useEffect(() => {
    if (liveThemes !== null) {
      syncThemes(liveThemes.items, liveThemes.actions);
    }
  }, [liveThemes, syncThemes]);
  const view = rowWindow(s.rows, s.cursor, visibleRowBudget(termRows));
  const llmSelection = s.categoryId === 'llm' ? s.rows[s.cursor] : undefined;
  const llmProvider =
    llmSelection?.kind === 'llmProvider'
      ? s.llm.providers?.[llmSelection.providerId]
      : undefined;
  const llmPolicy =
    llmSelection?.kind === 'llmPolicy'
      ? s.llm.policies?.[llmSelection.policyId]
      : undefined;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={width}
      height={height}
    >
      <Box flexShrink={0}>
        <Text bold color={theme.heading}>
          Settings
        </Text>
      </Box>

      <Box marginTop={1} flexDirection="row" flexGrow={1} flexBasis={0} minHeight={0}>
        <Box flexDirection="column" flexShrink={0} width={18} marginRight={2}>
          {SETTINGS_CATEGORIES.map((category, index) => {
            const selected = category.id === s.categoryId;
            const focused = s.activePane === 'categories' && index === s.categoryCursor;
            const cursor = focused ? '› ' : '  ';
            const mark = selected ? '• ' : '  ';
            return (
              <Box key={category.id} flexShrink={0}>
                <Text
                  color={focused ? theme.warning : selected ? theme.heading : theme.text}
                  bold={focused || selected}
                >
                  {cursor}
                  {mark}
                  {category.label}
                </Text>
              </Box>
            );
          })}
        </Box>

        <Box
          flexDirection="column"
          flexGrow={s.categoryId !== 'llm' ? 1 : 0}
          minHeight={0}
          width={s.categoryId === 'llm' ? 28 : undefined}
          flexShrink={s.categoryId === 'llm' ? 0 : undefined}
          marginRight={s.categoryId === 'llm' ? 2 : 0}
        >
          {view.before === 0 &&
            Array.from({ length: s.categoryCursor }, (_, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: these are positional spacer rows.
              <Box key={`category-offset-${i}`} flexShrink={0}>
                <Text> </Text>
              </Box>
            ))}
          {view.before > 0 && (
            <Box flexShrink={0}>
              <Text dimColor>{`  ↑ ${view.before} more`}</Text>
            </Box>
          )}
          {view.rows.map(({ row, index }) => (
            <RowView
              key={rowKey(row)}
              row={row}
              rowIndex={index}
              focused={s.activePane === 'settings' && index === s.cursor}
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
        {s.categoryId === 'llm' && (
          <Box flexDirection="column" flexGrow={1} flexBasis={0} minHeight={0}>
            <Text color={theme.muted} wrap="truncate-end">
              {s.llmForm?.kind === 'provider'
                ? `Settings / LLM Functionality / Providers / ${s.llmForm.providerId ?? 'New provider'}`
                : s.llmForm?.kind === 'policy'
                  ? `Settings / LLM Functionality / Policies / ${s.llmForm.policyId ?? 'New policy'}`
                  : 'Settings / LLM Functionality'}
            </Text>
            <Box marginTop={1} flexDirection="column">
              {s.llmForm?.kind === 'provider' && (
                <>
                  <Text bold color={theme.heading}>{s.llmForm.providerId === null ? `Add ${s.llmForm.providerType === 'lemonade' ? 'Lemonade' : 'OpenAI-compatible'}` : 'Provider settings'}</Text>
                  <LlmFormField label="Name" value={s.llmForm.name} editing={s.llmFormEditing && s.llmForm.field === 0} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 0} theme={theme} />
                  <Text color={s.activePane === 'editor' && (s.llmForm.field === 1 || s.llmForm.field === 2) ? theme.warning : theme.text} bold={s.activePane === 'editor' && (s.llmForm.field === 1 || s.llmForm.field === 2)} wrap="truncate-end">{`${s.activePane === 'editor' && (s.llmForm.field === 1 || s.llmForm.field === 2) ? '› ' : '  '}API key: ${s.llmForm.apiKey ? 'set' : 'not set'}; Endpoint`}</Text>
                  <Text color={s.activePane === 'editor' && s.llmForm.field === 3 ? theme.warning : theme.text} bold={s.activePane === 'editor' && s.llmForm.field === 3}>{`${s.activePane === 'editor' && s.llmForm.field === 3 ? '› ' : '  '}Models source     ${s.llmForm.source}`}</Text>
                  <LlmFormField label="Include models" value={s.llmForm.include} editing={s.llmFormEditing && s.llmForm.field === 4} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 4} theme={theme} />
                  <LlmFormField label="Exclude models" value={s.llmForm.exclude} editing={s.llmFormEditing && s.llmForm.field === 5} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 5} theme={theme} />
                  <LlmFormField label="Model overrides JSON" value={s.llmForm.overrides} editing={s.llmFormEditing && s.llmForm.field === 6} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 6} theme={theme} />
                  <Text color={theme.muted} wrap="truncate-end">{effectiveCatalogPreview(s.llmForm.include, s.llmForm.exclude, s.llmForm.overrides)}</Text>
                  <Text color={s.activePane === 'editor' && s.llmForm.field === 7 ? theme.warning : theme.text} bold={s.activePane === 'editor' && s.llmForm.field === 7}>{`${s.activePane === 'editor' && s.llmForm.field === 7 ? '› ' : '  '}Save`}</Text>
                  <Text color={s.activePane === 'editor' && s.llmForm.field === 8 ? theme.warning : theme.text} bold={s.activePane === 'editor' && s.llmForm.field === 8}>{`${s.activePane === 'editor' && s.llmForm.field === 8 ? '› ' : '  '}Cancel${s.llmForm.providerId !== null && !s.llmForm.builtin ? '  (d deletes)' : ''}`}</Text>
                </>
              )}
              {s.llmForm?.kind === 'policy' && (
                <>
                  <Text bold color={theme.heading}>{s.llmForm.policyId === null ? 'Create Policy' : 'Custom policy'}</Text>
                  <LlmFormField label="Name" value={s.llmForm.name} editing={s.llmFormEditing && s.llmForm.field === 0} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 0} theme={theme} />
                  <LlmFormField label="Groups JSON" value={s.llmForm.groups} editing={s.llmFormEditing && s.llmForm.field === 1} editValue={s.editValue} focused={s.activePane === 'editor' && s.llmForm.field === 1} theme={theme} />
                  <Text color={s.activePane === 'editor' && s.llmForm.field === 2 ? theme.warning : theme.text} bold={s.activePane === 'editor' && s.llmForm.field === 2}>{`${s.activePane === 'editor' && s.llmForm.field === 2 ? '› ' : '  '}Save`}</Text>
                  <Text color={s.activePane === 'editor' && s.llmForm.field === 3 ? theme.warning : theme.text} bold={s.activePane === 'editor' && s.llmForm.field === 3}>{`${s.activePane === 'editor' && s.llmForm.field === 3 ? '› ' : '  '}Cancel${s.llmForm.policyId !== null ? '  (d deletes)' : ''}`}</Text>
                </>
              )}
              {s.llmForm === null && (
                <>
              {llmSelection?.kind === 'llmGlobal' && (
                <>
                  <Text bold color={theme.heading}>LLM Functionality</Text>
                  <Text color={theme.text}>{`Enabled           ${s.llm.disabled ? '[ ]' : '[x]'}`}</Text>
                  <Text color={theme.muted}>Disabling preserves providers and policies.</Text>
                </>
              )}
              {llmSelection?.kind === 'llmProvider' && (
                <>
                  <Text bold color={theme.heading}>{llmProvider?.name ?? llmSelection.providerId}</Text>
                  <Text color={theme.text}>{`Enabled           ${(llmProvider?.enabled ?? false) ? '[x]' : '[ ]'}`}</Text>
                  <Text color={theme.text}>{`Endpoint          ${llmProvider?.endpoint ?? 'default'}`}</Text>
                  <Text color={theme.text}>{`API Key           ${llmProvider?.auth?.api_key ?? llmProvider?.api_key ? 'set' : 'not set'}`}</Text>
                  <Text color={theme.text}>{`Models            ${llmProvider?.models?.source ?? 'recommended'}`}</Text>
                </>
              )}
              {llmSelection?.kind === 'llmPolicy' && (
                <>
                  <Text bold color={theme.heading}>{llmPolicy?.name ?? llmSelection.policyId}</Text>
                  <Text color={theme.text}>{`Active            ${s.llm.active_policy === llmSelection.policyId ? '[x]' : '[ ]'}`}</Text>
                  <Text color={theme.muted}>{llmSelection.builtin ? 'Built-in policy (read-only)' : 'Custom policy'}</Text>
                </>
              )}
              {llmSelection?.kind === 'llmAddProvider' && (
                <>
                  <Text bold color={theme.heading}>{llmSelection.providerType === 'lemonade' ? 'Add Lemonade' : 'Add OpenAI-compatible'}</Text>
                  <Text color={theme.muted}>Enter opens the configuration form.</Text>
                </>
              )}
              {llmSelection?.kind === 'llmCreatePolicy' && (
                <>
                  <Text bold color={theme.heading}>Create Policy</Text>
                  <Text color={theme.muted}>Enter opens the policy creation form.</Text>
                </>
              )}
                </>
              )}
            </Box>
          </Box>
        )}
      </Box>

      <Box flexShrink={0} flexDirection="column">
        {!ctrlAvailable && (
          <Box marginTop={1} flexShrink={0}>
            <Text color={theme.muted}>{CTRL_UNSUPPORTED_NOTICE}</Text>
          </Box>
        )}

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

        {s.categoryId === 'workspaces' &&
          s.showKittyWorkspaceMappingWarning &&
          s.modifier === 'ctrl' && (
            <Box marginTop={1} flexShrink={0}>
              <Text color={theme.error}>{KITTY_WORKSPACE_MAPPING_WARNING}</Text>
            </Box>
          )}

        {s.notice !== null && (
          <Box marginTop={1} flexShrink={0}>
            <Text color={theme.warning}>{s.notice}</Text>
          </Box>
        )}

        <Box marginTop={1} flexShrink={0}>
          <Text dimColor>
            j/k: navigate · h/l: categories/settings · enter: select · esc: close
          </Text>
        </Box>
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
const VISIBLE_ROWS_MAX = 22;

/** Rows of modal chrome outside the scroll list (title, borders, footer hints, main margin). */
const MODAL_CHROME_ROWS = 8;

/** Fit the scroll window to the fixed ~80% modal shell so focused rows stay on-screen. */
function visibleRowBudget(termRows: number): number {
  const modalRows = Math.max(12, Math.floor(termRows * 0.8));
  return Math.max(6, Math.min(VISIBLE_ROWS_MAX, modalRows - MODAL_CHROME_ROWS));
}

/** Compute the visible row window around the cursor. Returns the slice (with original indices, so
 * focus math stays correct) plus the count of rows hidden above/below (shown as "↑ N more"). */
function rowWindow(
  rows: readonly SettingsRow[],
  cursor: number,
  visibleRows: number,
): { rows: Array<{ row: SettingsRow; index: number }>; before: number; after: number } {
  if (rows.length <= visibleRows) {
    return {
      rows: rows.map((row, index) => ({ row, index })),
      before: 0,
      after: 0,
    };
  }
  // Centre the window on the cursor, clamped to the ends.
  const half = Math.floor(visibleRows / 2);
  let start = Math.max(0, cursor - half);
  const end = Math.min(rows.length, start + visibleRows);
  start = Math.max(0, end - visibleRows);
  const slice: Array<{ row: SettingsRow; index: number }> = [];
  for (let i = start; i < end; i++) {
    const row = rows[i];
    if (row !== undefined) {
      slice.push({ row, index: i });
    }
  }
  return { rows: slice, before: start, after: rows.length - end };
}

/** A stable React key for a row. */
function rowKey(row: SettingsRow): string {
  return row.id;
}

function parseModelList(value: string): string[] {
  return [...new Set(value.split(',').map((model) => model.trim()).filter(Boolean))];
}

function effectiveCatalogPreview(include: string, exclude: string, overrides: string): string {
  const enabled = parseModelList(include).filter((model) => !parseModelList(exclude).includes(model));
  let overrideCount = 0;
  try {
    overrideCount = Object.keys(JSON.parse(overrides || '{}') as object).length;
  } catch {
    return 'Effective catalog: overrides JSON is invalid';
  }
  return `Effective catalog: ${enabled.length ? enabled.join(', ') : 'source models'}${overrideCount ? `; ${overrideCount} override${overrideCount === 1 ? '' : 's'}` : ''}`;
}

function LlmFormField({
  label, value, editing, editValue, focused, theme,
}: {
  readonly label: string;
  readonly value: string;
  readonly editing: boolean;
  readonly editValue: string;
  readonly focused: boolean;
  readonly theme: ReturnType<typeof useTheme>;
}): JSX.Element {
  return (
    <Box flexShrink={0}>
      <Text color={focused ? theme.warning : theme.text} bold={focused}>{`${focused ? '› ' : '  '}${label}`}</Text>
      <Text color={theme.muted}>{'  '}</Text>
      {editing ? <TextInput value={editValue} placeholder={label} focused color={theme.text} /> : <Text color={theme.muted} wrap="truncate-end">{value || 'not set'}</Text>}
    </Box>
  );
}

/** Render one flat row by kind. */
function RowView({
  row,
  rowIndex,
  focused,
  state: s,
  theme,
  ctrlAvailable,
}: {
  readonly row: SettingsRow;
  readonly rowIndex: number;
  readonly focused: boolean;
  readonly state: SettingsState;
  readonly theme: ReturnType<typeof useTheme>;
  readonly ctrlAvailable: boolean;
}): JSX.Element {
  if (row.kind === 'header') {
    return (
      <Box marginTop={rowIndex === 0 ? 0 : 1} flexShrink={0}>
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
    const confirming = s.confirmingThemeDelete === row.value;
    const label = row.builtin ? row.name : `${row.name} (custom)`;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {mark}
          {label}
        </Text>
        <Text color={confirming ? theme.warning : theme.muted}>
          {confirming ? '  delete? (y/n)' : row.builtin ? '' : '  d: delete'}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'themeImport') {
    const importing = s.editing?.kind === 'themeImport';
    const color = focused ? theme.warning : theme.text;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {importing ? 'paste JSON ' : '+ Import Theme'}
        </Text>
        {importing ? (
          <TextInput value={s.editValue} placeholder="(json)" focused color={theme.text} />
        ) : null}
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

  if (row.kind === 'workspaceCount') {
    const selected = row.value === s.workspaceCount;
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

  if (row.kind === 'barWidget') {
    const config = resolveBarWidgetConfig(row.widgetId, s.barWidgets);
    const selected =
      row.field === 'enabled'
        ? row.value === config.enabled
        : row.field === 'adaptive'
          ? row.value === config.adaptive
          : row.value === config.placement;
    const mark = selected ? '(•) ' : '( ) ';
    const color = focused ? theme.warning : theme.text;
    const label =
      row.field === 'enabled'
        ? row.value
          ? 'on'
          : 'off'
        : row.field === 'adaptive'
          ? row.value
            ? 'adaptive'
            : 'show all'
          : row.value === 'top'
            ? 'top bar'
            : 'bottom bar';
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

  if (row.kind === 'barWidgetHarness') {
    const config = resolveBarWidgetConfig(row.widgetId, s.barWidgets);
    const selected = config.harnesses;
    const checked =
      selected === undefined || selected.length === 0 ? true : selected.includes(row.value);
    const mark = checked ? '[x] ' : '[ ] ';
    const color = focused ? theme.warning : theme.text;
    const label = harnessShortLabel(row.value);
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
      const label = harnessLabel(row.value) ?? row.value;
      return (
        <Box flexShrink={0}>
          <Text color={color} bold={focused}>
            {cursor}
            {selected ? '(•) ' : '( ) '}
            {label}
          </Text>
        </Box>
      );
    }
    if (row.field === 'model') {
      const selected = sr !== null && sr.model === row.value;
      const label = row.label ?? (row.value === '' ? '(default model)' : row.value);
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
    const effective = harnessLabel(s.effectiveCollaborator) ?? s.effectiveCollaborator;
    const label = isDefaultRow ? `(default) ${effective}` : (harnessLabel(row.value) ?? '');
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
    const effective = harnessLabel(s.effectivePlanner) ?? s.effectivePlanner;
    const label = isDefaultRow ? `(default) ${effective}` : (harnessLabel(row.value) ?? '');
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
            {`reset to default (${s.effectiveCrow
              .map((harness) => harnessLabel(harness) ?? harness)
              .join(', ')})`}
          </Text>
        </Box>
      );
    }
    const checked = pool.includes(row.value as string);
    const mark = checked ? '[x] ' : '[ ] ';
    const label = harnessLabel(row.value) ?? row.value;
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

  if (row.kind === 'llmGlobal') {
    const enabled = !s.llm.disabled;
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused}>
          {cursor}
          {enabled ? '[x] Enabled' : '[ ] Enabled'}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'llmProvider') {
    const provider = s.llm.providers?.[row.providerId];
    const enabled = provider?.enabled ?? false;
    const label = provider?.name ?? row.providerId.replaceAll('-', ' ');
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused}>
          {cursor}
          {enabled ? '✓ ' : '○ '}
          {label}
          {!row.builtin ? <Text color={theme.muted}> {'  custom'}</Text> : null}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'llmAddProvider') {
    const label = row.providerType === 'lemonade' ? 'Add Lemonade' : 'Add OpenAI-compatible';
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused}>
          {cursor}+ {label}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'llmPolicy') {
    const policy = s.llm.policies?.[row.policyId];
    const label = policy?.name ?? row.policyId.replaceAll('-', ' ');
    const active = s.llm.active_policy === row.policyId;
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused}>
          {cursor}
          {active ? '● ' : '  '}
          {label}
          {row.builtin ? <Text color={theme.muted}> {'  built-in'}</Text> : null}
        </Text>
      </Box>
    );
  }

  if (row.kind === 'llmCreatePolicy') {
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused}>
          {cursor}+ Create Policy
        </Text>
      </Box>
    );
  }

  if (row.kind === 'llmFeaturePolicy') {
    const policyId = s.llm.feature_policies?.[row.feature] ?? s.llm.active_policy ?? 'local-then-free';
    return (
      <Box flexShrink={0}>
        <Text color={focused ? theme.warning : theme.text} bold={focused} wrap="truncate-end">
          {cursor}
          {`${row.feature.replaceAll('_', ' ')}: ${policyId}`}
        </Text>
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
          no templates
        </Text>
      </Box>
    );
  }

  if (row.kind === 'templateCreate') {
    const creatingName = s.editing?.kind === 'templateCreateName';
    const creatingBody = s.editing?.kind === 'templateCreateBody';
    const creatingBodyName = s.editing?.kind === 'templateCreateBody' ? s.editing.name : null;
    const color = focused ? theme.warning : theme.text;
    return (
      <Box flexShrink={0}>
        <Text color={color} bold={focused}>
          {cursor}
          {creatingName ? 'name ' : creatingBody ? `body :${creatingBodyName}: ` : '+ New Template'}
        </Text>
        {creatingName || creatingBody ? (
          <TextInput
            value={s.editValue}
            placeholder={creatingName ? '(name)' : '(body)'}
            focused
            color={theme.text}
          />
        ) : null}
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
  const label = bindingLabel(row.action, s.modifier, ctrlAvailable, s.overrides);
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
