/**
 * SettingsPanel — Appearance / keybindings / bars / workspaces / harnesses / LLM.
 * Persists via `settings.update`; theme also calls `setTheme` for instant repaint. Mirrors
 * ui-core declarative settings categories where practical (Wave C1/C2 depth). LLM depth (providers,
 * model catalog, policy groups, resolution preview) lives in {@link LlmSettingsSection}.
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { useEffect, useState } from 'react';
import { shallow } from 'zustand/shallow';
import {
  DEFAULT_THEME_ID,
  getPalette,
  getThemeMeta,
  listThemeIds,
} from '@murder/ui-core/theme/palettes.js';
import type { ThemeId } from '@murder/ui-core/theme/palettes.js';
import { setTheme, useThemeId } from '@murder/ui-core/theme/themeStore.js';
import {
  defaultEffortFor,
  defaultModelFor,
  HARNESSES,
  startupRogueEffortsFor,
  startupRogueModelsFor,
} from '@murder/ui-core/components/settings/items/harnesses.js';
import {
  BACKGROUND_TRANSPARENCY_OPTIONS,
} from '@murder/ui-core/components/settings/items/appearance.js';
import {
  MODIFIERS,
  REBINDABLE,
} from '@murder/ui-core/components/settings/items/keybindings.js';
import { WORKSPACE_COUNT_OPTIONS } from '@murder/ui-core/components/settings/items/workspaces.js';
import {
  BAR_WIDGET_DEFINITIONS,
  resolveBarWidgetConfig,
  type BarWidgetId,
  type BarWidgetsConfig,
} from '@murder/ui-core/selectors/barWidgetRegistry.js';
import { ACTIONS, type ActionId } from '@murder/ui-core/input/bindings.js';
import type {
  ClaudeControlBackend,
  CodexControlBackend,
  CursorControlBackend,
  DefaultChatViewMode,
  DocumentDisplayMode,
} from '@murder/ui-core/store/settings/settingsSlice.js';
import { useCreationDialogs } from '../../creationDialogs.js';
import { bindingKeyLabel, validateRebindCapture } from '../../keybindCapture.js';
import { Panel, Input, Select, Radio, Switch, Checkbox, Button, cx } from '../ds/index.js';
import { LlmSettingsSection } from './LlmSettingsSection.js';

const FALLBACK_THEME_IDS = listThemeIds();

/** Mirrored from the shared harness settings model (not exported there). */
const CODEX_CONTROL_BACKENDS: readonly CodexControlBackend[] = ['harness_parse', 'app_server'];
const CURSOR_CONTROL_BACKENDS: readonly CursorControlBackend[] = ['harness_parse', 'acp'];
const CLAUDE_CONTROL_BACKENDS: readonly ClaudeControlBackend[] = ['harness_parse', 'agent_sdk'];

const CHAT_VIEW_OPTIONS: readonly DefaultChatViewMode[] = ['verbose', 'condensed'];
const DOCUMENT_DISPLAY_OPTIONS: readonly DocumentDisplayMode[] = ['plain', 'markdown'];

function actionDefaultKey(action: ActionId): string {
  const def = ACTIONS[action].default;
  return def.kind === 'command' ? def.key : '';
}

export function SettingsPanel(): React.JSX.Element {
  const settings = useAppStore((s) => s.settings, shallow);
  const themes = useAppStore((s) => s.themes.items, shallow);
  const update = useAppStore((s) => s.actions.settings.update);
  const loadThemes = useAppStore((s) => s.actions.themes.load);
  const importTheme = useAppStore((s) => s.actions.themes.importTheme);
  const removeTheme = useAppStore((s) => s.actions.themes.remove);
  const activeTheme = useThemeId();
  const { openPromptTemplates } = useCreationDialogs();
  const [themeJson, setThemeJson] = useState('');
  const [themeImportError, setThemeImportError] = useState<string | null>(null);
  const [bindingNotice, setBindingNotice] = useState<string | null>(null);
  const [capturing, setCapturing] = useState<ActionId | null>(null);

  useEffect(() => {
    if (themes.length === 0) {
      void loadThemes();
    }
  }, [themes.length, loadThemes]);

  // Capture-to-rebind: listen for the next keydown while a binding row is armed.
  useEffect(() => {
    if (capturing === null) {
      return;
    }
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.repeat) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') {
        setCapturing(null);
        setBindingNotice(null);
        return;
      }
      // Reject modifier-only / non-printable (ctrl+x is never a bare rebind).
      if (e.ctrlKey || e.metaKey || e.altKey) {
        setBindingNotice('Rebinds are bare keys (no modifiers)');
        setCapturing(null);
        return;
      }
      const raw = e.key === ' ' ? ' ' : e.key.length === 1 ? e.key : '';
      if (raw === '') {
        setBindingNotice('Press a single printable key (Esc to cancel)');
        setCapturing(null);
        return;
      }
      const result = validateRebindCapture(capturing, raw, settings.keyOverrides);
      if (!result.ok) {
        setBindingNotice(result.notice);
        setCapturing(null);
        return;
      }
      const overrides = { ...settings.keyOverrides };
      const def = actionDefaultKey(capturing);
      if (result.key === def) {
        delete overrides[capturing];
      } else {
        overrides[capturing] = result.key;
      }
      setBindingNotice(null);
      setCapturing(null);
      void update({ key_overrides: overrides });
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [capturing, settings.keyOverrides, update]);

  // Honour background transparency as chrome opacity (browser has no terminal canvas behind).
  useEffect(() => {
    const t = Math.max(0, Math.min(100, settings.backgroundTransparency));
    document.documentElement.style.setProperty('--background-transparency', String(t));
    // 0 = solid → opacity 1; 100 = see-through → soft translucent chrome (floor so UI stays readable).
    const opacity = Math.max(0.55, 1 - t / 100);
    document.documentElement.style.setProperty('--chrome-opacity', String(opacity));
  }, [settings.backgroundTransparency]);

  const themeIds = themes.length > 0 ? themes.map((t) => t.id) : [...FALLBACK_THEME_IDS];

  const chooseTheme = (id: ThemeId): void => {
    setTheme(id);
    void update({ theme: id });
  };

  // The collaborator-harness override falls back to the daemon's live effective value when unset.
  const harnessValue = settings.collaboratorHarness ?? settings.effectiveCollaboratorHarness;
  const harnessOptions = Array.from(
    new Set([settings.effectiveCollaboratorHarness, ...settings.effectiveCrowHarnesses, harnessValue]),
  );
  // Planner: Select includes an explicit "default" (null) option; label shows effective fallback.
  const plannerOptions = [
    { value: '', label: `default (${settings.effectivePlannerHarness})` },
    ...HARNESSES,
  ];
  // Crow pool: override when set, else the daemon's effective pool. Empty override = use default.
  const crowPool = settings.crowHarnesses ?? settings.effectiveCrowHarnesses;
  const crowUsingDefault = settings.crowHarnesses === null;
  const startupRogue = settings.startupRogue;
  const startupHarness = startupRogue?.harness ?? '';
  const startupModelChoices =
    startupRogue === null
      ? []
      : startupRogueModelsFor(startupRogue.harness, settings.startupRogueModels);
  const startupEffortChoices =
    startupRogue === null
      ? []
      : startupRogueEffortsFor(startupRogue.harness, settings.startupRogueEfforts);
  const startupModelIds = new Set(startupModelChoices.map((m) => m.id));
  const startupModelValue =
    startupRogue !== null && startupModelIds.has(startupRogue.model)
      ? startupRogue.model
      : (startupModelChoices[0]?.id ?? '');
  const startupEffortValue =
    startupRogue !== null &&
    startupRogue.effort !== null &&
    startupEffortChoices.includes(startupRogue.effort)
      ? startupRogue.effort
      : (startupEffortChoices[0] ?? '');

  const chooseStartupHarness = (harness: string): void => {
    if (harness === '') {
      void update({ startup_rogue: null });
      return;
    }
    const same = startupRogue !== null && startupRogue.harness === harness;
    const efforts = startupRogueEffortsFor(harness, settings.startupRogueEfforts);
    void update({
      startup_rogue: {
        harness,
        model: same ? startupRogue.model : defaultModelFor(harness, settings.startupRogueModels),
        effort:
          same && startupRogue.effort !== null && efforts.includes(startupRogue.effort)
            ? startupRogue.effort
            : defaultEffortFor(harness, settings.startupRogueEfforts),
      },
    });
  };

  /** Toggle a crow harness in/out of the pool, or reset to the effective default. Mirrors inktui. */
  const toggleCrow = (value: string | null): void => {
    if (value === null) {
      void update({ crow_harnesses: null });
      return;
    }
    const current = settings.crowHarnesses ?? [...settings.effectiveCrowHarnesses];
    const checked = current.includes(value);
    if (checked && current.length === 1) {
      return;
    }
    const next = checked ? current.filter((h) => h !== value) : [...current, value];
    void update({ crow_harnesses: next });
  };

  const setBarWidgetEnabled = (widgetId: BarWidgetId, enabled: boolean): void => {
    const current = resolveBarWidgetConfig(widgetId, settings.barWidgets);
    const next: BarWidgetsConfig = {
      ...settings.barWidgets,
      [widgetId]: { ...current, enabled },
    };
    void update({ bar_widgets: next });
  };

  const setBinding = (action: ActionId, key: string): void => {
    const overrides = { ...settings.keyOverrides };
    const def = actionDefaultKey(action);
    if (key === '' || key === def) {
      delete overrides[action];
      setBindingNotice(null);
      void update({ key_overrides: overrides });
      return;
    }
    const result = validateRebindCapture(action, key, settings.keyOverrides);
    if (!result.ok) {
      setBindingNotice(result.notice);
      return;
    }
    overrides[action] = result.key;
    setBindingNotice(null);
    void update({ key_overrides: overrides });
  };

  const beginCapture = (action: ActionId): void => {
    setCapturing(action);
    setBindingNotice(`Press a key to bind "${ACTIONS[action].description}" (Esc to cancel)`);
  };

  const clearBinding = (action: ActionId): void => {
    setCapturing(null);
    setBinding(action, '');
  };

  const submitThemeImport = (): void => {
    const json = themeJson.trim();
    if (json === '') {
      setThemeImportError('Paste a theme JSON blob to import.');
      return;
    }
    setThemeImportError(null);
    void importTheme(json)
      .then((id) => {
        setThemeJson('');
        chooseTheme(id as ThemeId);
      })
      .catch((error: unknown) => {
        setThemeImportError(error instanceof Error ? error.message : String(error));
      });
  };

  return (
    <Panel title="settings" data-panel-id="settings">
      <div className="settings">
        <section className="settings__group">
          <h3 className="settings__heading">theme</h3>
          <div className="settings__themes">
            {themeIds.map((id) => {
              const p = getPalette(id);
              const record = themes.find((t) => t.id === id);
              const label = record?.name ?? getThemeMeta(id)?.name ?? id;
              const builtin = record?.builtin ?? getThemeMeta(id)?.builtin ?? true;
              if (p === undefined) {
                return null;
              }
              return (
                <div key={id} className="settings__theme-row">
                  <button
                    type="button"
                    className={cx('theme-swatch', id === activeTheme && 'theme-swatch--on')}
                    data-on={id === activeTheme}
                    onClick={() => chooseTheme(id)}
                    style={
                      {
                        '--swatch-surface': p.bg0,
                        '--swatch-accent': p.green,
                      } as React.CSSProperties
                    }
                  >
                    <span className="theme-swatch__chip" aria-hidden="true" />
                    <span className="theme-swatch__label">{label}</span>
                  </button>
                  {!builtin ? (
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => {
                        void removeTheme(id).then(() => {
                          if (activeTheme === id) {
                            chooseTheme(DEFAULT_THEME_ID);
                          }
                        });
                      }}
                    >
                      remove
                    </Button>
                  ) : null}
                </div>
              );
            })}
          </div>
          <Input
            multiline
            rows={3}
            label="import theme JSON"
            value={themeJson}
            onChange={(e) => setThemeJson(e.target.value)}
            {...(themeImportError !== null
              ? { hint: themeImportError, invalid: true as const }
              : {})}
          />
          <Button type="button" onClick={submitThemeImport}>
            Import theme
          </Button>
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">background transparency</h3>
          <Radio
            name="background_transparency"
            inline
            options={BACKGROUND_TRANSPARENCY_OPTIONS.map((v) => ({
              value: String(v),
              label: `${v}%`,
            }))}
            value={String(settings.backgroundTransparency)}
            onChange={(v) => void update({ background_transparency: Number(v) })}
          />
          <p className="settings__hint">Applied as chrome opacity in the browser (no terminal canvas).</p>
        </section>

        <Input
          type="number"
          min={0}
          max={4}
          label="pane gap"
          className="settings__stepper"
          value={settings.paneGap}
          onChange={(e) => void update({ pane_gap: Number(e.target.value) })}
        />

        <section className="settings__group">
          <h3 className="settings__heading">default chat view</h3>
          <Radio
            name="default_chat_view"
            inline
            options={[...CHAT_VIEW_OPTIONS]}
            value={settings.defaultChatViewMode}
            onChange={(v) => void update({ default_chat_view_mode: v as DefaultChatViewMode })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">document display</h3>
          <Radio
            name="document_display"
            inline
            options={[...DOCUMENT_DISPLAY_OPTIONS]}
            value={settings.documentDisplayMode}
            onChange={(v) => void update({ document_display_mode: v as DocumentDisplayMode })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">keyboard modifier</h3>
          <Radio
            inline
            options={[...MODIFIERS]}
            value={settings.modifier}
            onChange={(v) => void update({ modifier: v as typeof settings.modifier })}
          />
        </section>

        <Switch
          label="vim mode"
          checked={settings.vimMode}
          onChange={(e) => void update({ vim_mode: e.target.checked })}
        />

        <section className="settings__group">
          <h3 className="settings__heading">keybindings</h3>
          <div className="settings__bindings">
            {REBINDABLE.map((action) => {
              const current = settings.keyOverrides[action] ?? '';
              const def = actionDefaultKey(action);
              const display = bindingKeyLabel(current, def);
              const isCapturing = capturing === action;
              return (
                <div key={action} className="settings__binding-row">
                  <span className="settings__binding-label">{ACTIONS[action].description}</span>
                  <kbd className="settings__binding-key">{isCapturing ? '…' : display}</kbd>
                  <Button
                    type="button"
                    size="sm"
                    variant={isCapturing ? 'primary' : 'secondary'}
                    aria-label={`rebind ${ACTIONS[action].description}`}
                    onClick={() => beginCapture(action)}
                  >
                    {isCapturing ? 'Press key…' : 'Rebind'}
                  </Button>
                  {current !== '' ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`reset ${ACTIONS[action].description}`}
                      onClick={() => clearBinding(action)}
                    >
                      Reset
                    </Button>
                  ) : null}
                </div>
              );
            })}
          </div>
          {bindingNotice !== null ? (
            <p
              className={cx(
                'settings__hint',
                capturing === null && !bindingNotice.startsWith('Press a key')
                  ? 'settings__hint--error'
                  : null,
              )}
            >
              {bindingNotice}
            </p>
          ) : (
            <p className="settings__hint">Click Rebind, then press a key. Esc cancels.</p>
          )}
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">workspaces</h3>
          <Select
            label="workspace count"
            options={WORKSPACE_COUNT_OPTIONS.map((n) => ({ value: String(n), label: String(n) }))}
            value={String(settings.workspaceCount)}
            onChange={(e) => void update({ workspace_count: Number(e.target.value) })}
          />
          <p className="settings__hint">
            Count 1 hides the switcher. Digits in the nav beam jump; {`<Cmd>+Shift+J/K`} cycles.
          </p>
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">bars</h3>
          {BAR_WIDGET_DEFINITIONS.map((def) => {
            const config = resolveBarWidgetConfig(def.id, settings.barWidgets);
            return (
              <Switch
                key={def.id}
                label={def.label}
                checked={config.enabled}
                onChange={(e) => setBarWidgetEnabled(def.id, e.target.checked)}
              />
            );
          })}
        </section>

        <Select
          label="collaborator harness"
          options={harnessOptions}
          value={harnessValue}
          onChange={(e) => void update({ collaborator_harness: e.target.value })}
        />

        <section className="settings__group">
          <Select
            label="startup rogue"
            options={[{ value: '', label: 'off' }, ...HARNESSES]}
            value={startupHarness}
            onChange={(e) => chooseStartupHarness(e.target.value)}
          />
          {startupRogue !== null ? (
            <div className="settings__inline">
              <Select
                label="startup model"
                options={startupModelChoices.map((m) => ({ value: m.id, label: m.label }))}
                value={startupModelValue}
                onChange={(e) =>
                  void update({
                    startup_rogue: { ...startupRogue, model: e.target.value },
                  })
                }
              />
              {startupEffortChoices.length > 0 ? (
                <Select
                  label="startup effort"
                  options={[...startupEffortChoices]}
                  value={startupEffortValue}
                  onChange={(e) =>
                    void update({
                      startup_rogue: { ...startupRogue, effort: e.target.value },
                    })
                  }
                />
              ) : null}
            </div>
          ) : null}
        </section>

        <Select
          label="planner harness"
          options={plannerOptions}
          value={settings.plannerHarness ?? ''}
          onChange={(e) => void update({ planner_harness: e.target.value === '' ? null : e.target.value })}
        />

        <section className="settings__group">
          <h3 className="settings__heading">crow harnesses</h3>
          <Switch
            label="use default pool"
            checked={crowUsingDefault}
            onChange={(e) => {
              if (e.target.checked) {
                toggleCrow(null);
              } else {
                void update({ crow_harnesses: [...settings.effectiveCrowHarnesses] });
              }
            }}
          />
          <div className="settings__inline">
            {HARNESSES.map((h) => (
              <Checkbox
                key={h}
                label={h}
                checked={crowPool.includes(h)}
                onChange={() => toggleCrow(h)}
              />
            ))}
          </div>
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">codex control backend</h3>
          <Radio
            name="codex_control_backend"
            inline
            options={[...CODEX_CONTROL_BACKENDS]}
            value={settings.codexControlBackend}
            onChange={(v) => void update({ codex_control_backend: v as CodexControlBackend })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">cursor control backend</h3>
          <Radio
            name="cursor_control_backend"
            inline
            options={[...CURSOR_CONTROL_BACKENDS]}
            value={settings.cursorControlBackend}
            onChange={(v) => void update({ cursor_control_backend: v as CursorControlBackend })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">claude control backend</h3>
          <Radio
            name="claude_control_backend"
            inline
            options={[...CLAUDE_CONTROL_BACKENDS]}
            value={settings.claudeControlBackend}
            onChange={(v) => void update({ claude_control_backend: v as ClaudeControlBackend })}
          />
        </section>

        <LlmSettingsSection />

        <section className="settings__group">
          <h3 className="settings__heading">prompt templates</h3>
          <Button type="button" onClick={openPromptTemplates}>
            Open Prompt Templates…
          </Button>
        </section>

        {settings.status === 'error' ? (
          <p className="settings__hint settings__hint--error">
            {settings.error ?? 'Failed to load settings.'}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
