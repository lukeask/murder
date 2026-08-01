/**
 * SettingsPanel — theme, modifier, pane gap, vim, harnesses, crow pool, startup rogue, and
 * control backends. Persists via `settings.update`; theme also calls `setTheme` for instant repaint.
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { getPalette, getThemeMeta, listThemeIds } from '@murder/ui-core/theme/palettes.js';
import type { ThemeId } from '@murder/ui-core/theme/palettes.js';
import { setTheme, useThemeId } from '@murder/ui-core/theme/themeStore.js';
import {
  defaultEffortFor,
  defaultModelFor,
  HARNESSES,
  startupRogueEffortsFor,
  startupRogueModelsFor,
} from '@murder/ui-core/components/settings/items/harnesses.js';
import type {
  ClaudeControlBackend,
  CodexControlBackend,
  CursorControlBackend,
} from '@murder/ui-core/store/settings/settingsSlice.js';
import { Panel, Input, Select, Radio, Switch, Checkbox, cx } from '../ds/index.js';

const FALLBACK_THEME_IDS = listThemeIds();

const MODIFIER_OPTIONS = ['alt', 'ctrl', 'both'];

/** Mirrored from the shared harness settings model (not exported there). */
const CODEX_CONTROL_BACKENDS: readonly CodexControlBackend[] = ['harness_parse', 'app_server'];
const CURSOR_CONTROL_BACKENDS: readonly CursorControlBackend[] = ['harness_parse', 'acp'];
const CLAUDE_CONTROL_BACKENDS: readonly ClaudeControlBackend[] = ['harness_parse', 'agent_sdk'];

export function SettingsPanel(): React.JSX.Element {
  const settings = useAppStore((s) => s.settings, shallow);
  const themes = useAppStore((s) => s.themes.items, shallow);
  const update = useAppStore((s) => s.actions.settings.update);
  const loadThemes = useAppStore((s) => s.actions.themes.load);
  const activeTheme = useThemeId();

  useEffect(() => {
    if (themes.length === 0) {
      void loadThemes();
    }
  }, [themes.length, loadThemes]);

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

  return (
    <Panel title="settings" data-panel-id="settings">
      <div className="settings">
        <section className="settings__group">
          <h3 className="settings__heading">theme</h3>
          <div className="settings__themes">
            {themeIds.map((id) => {
              const p = getPalette(id);
              const label = themes.find((t) => t.id === id)?.name ?? getThemeMeta(id)?.name ?? id;
              if (p === undefined) {
                return null;
              }
              return (
                <button
                  key={id}
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
              );
            })}
          </div>
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">keyboard modifier (desktop)</h3>
          <Radio
            inline
            options={MODIFIER_OPTIONS}
            value={settings.modifier}
            onChange={(v) => void update({ modifier: v as typeof settings.modifier })}
          />
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

        <Switch
          label="vim mode"
          checked={settings.vimMode}
          onChange={(e) => void update({ vim_mode: e.target.checked })}
        />

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

        {settings.status === 'error' ? (
          <p className="settings__hint settings__hint--error">
            {settings.error ?? 'Failed to load settings.'}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
