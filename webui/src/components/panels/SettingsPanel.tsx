/**
 * SettingsPanel — the settings screen, reskinned onto the design system (Phase C2). Theme switching
 * is the headline feature: selecting a theme calls `setTheme(id)` (the same global themeStore the Ink
 * UI uses → repaints every CSS var via {@link useThemeCssVars}) AND persists it through
 * `settings.update({ theme })` so it survives a reload. Pane gap, the input modifier, vim mode,
 * collaborator/planner/crow harnesses, startup rogue, and harness control backends are also surfaced
 * (persisted via `settings.update`).
 *
 * ── THE LOCKED PANEL-REWRITE PATTERN (see TicketsPanel exemplar) ────────────────────────────────
 * Presentation moves onto DS primitives (Panel + form controls from the barrel); the data wiring is
 * UNCHANGED — same `s.settings` reads and `s.actions.settings.update`, and the theme control keeps
 * `useThemeId()` + `PALETTES` with the existing `chooseTheme` (setTheme for instant repaint, update to
 * persist). Each setting maps to its DS control: modifier → Radio, paneGap → numeric Input,
 * vimMode → Switch, collaborator/planner harness → Select, crow → Checkbox pool, control backends →
 * Radio, theme → selectable swatch toggles (`data-on`). Bespoke CSS lives in `styles/panels-settings.css`
 * (wired in by the shell, not imported here).
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { useEffect } from 'react';
import { shallow } from 'zustand/shallow';
import { getPalette, getThemeMeta, listThemeIds } from '@core/theme/palettes.js';
import type { ThemeId } from '@core/theme/palettes.js';
import { setTheme, useThemeId } from '@core/theme/themeStore.js';
import {
  defaultEffortFor,
  defaultModelFor,
  HARNESSES,
  startupRogueEffortsFor,
  startupRogueModelsFor,
} from '@core/components/settings/items/harnesses.js';
import type {
  ClaudeControlBackend,
  CodexControlBackend,
  CursorControlBackend,
} from '@core/store/settings/settingsSlice.js';
import { Panel, Input, Select, Radio, Switch, Checkbox, cx } from '../ds/index.js';

const FALLBACK_THEME_IDS = listThemeIds();

const MODIFIER_OPTIONS = [
  { value: 'alt', label: 'alt' },
  { value: 'ctrl', label: 'ctrl' },
  { value: 'both', label: 'both' },
];

/** Mirrored from `@core/.../harnesses` (not exported there). */
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
  ).map((h) => ({ value: h, label: h }));
  // Planner: Select includes an explicit "default" (null) option; label shows effective fallback.
  const plannerOptions = [
    { value: '', label: `default (${settings.effectivePlannerHarness})` },
    ...HARNESSES.map((h) => ({ value: h, label: h })),
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

  const choosePlannerHarness = (value: string): void => {
    void update({ planner_harness: value === '' ? null : value });
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

        <section className="settings__group">
          <Input
            type="number"
            min={0}
            max={4}
            label="pane gap"
            className="settings__stepper"
            value={settings.paneGap}
            onChange={(e) => void update({ pane_gap: Number(e.target.value) })}
          />
        </section>

        <section className="settings__group">
          <Switch
            label="vim mode"
            checked={settings.vimMode}
            onChange={(e) => void update({ vim_mode: e.target.checked })}
          />
        </section>

        <section className="settings__group">
          <Select
            label="collaborator harness"
            options={harnessOptions}
            value={harnessValue}
            onChange={(e) => void update({ collaborator_harness: e.target.value })}
          />
        </section>

        <section className="settings__group">
          <Select
            label="startup rogue"
            options={[
              { value: '', label: 'off' },
              ...HARNESSES.map((h) => ({ value: h, label: h })),
            ]}
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
                  options={startupEffortChoices.map((effort) => ({ value: effort, label: effort }))}
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

        <section className="settings__group">
          <Select
            label="planner harness"
            options={plannerOptions}
            value={settings.plannerHarness ?? ''}
            onChange={(e) => choosePlannerHarness(e.target.value)}
          />
        </section>

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
            options={CODEX_CONTROL_BACKENDS.map((v) => ({ value: v, label: v }))}
            value={settings.codexControlBackend}
            onChange={(v) => void update({ codex_control_backend: v as CodexControlBackend })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">cursor control backend</h3>
          <Radio
            name="cursor_control_backend"
            inline
            options={CURSOR_CONTROL_BACKENDS.map((v) => ({ value: v, label: v }))}
            value={settings.cursorControlBackend}
            onChange={(v) => void update({ cursor_control_backend: v as CursorControlBackend })}
          />
        </section>

        <section className="settings__group">
          <h3 className="settings__heading">claude control backend</h3>
          <Radio
            name="claude_control_backend"
            inline
            options={CLAUDE_CONTROL_BACKENDS.map((v) => ({ value: v, label: v }))}
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
