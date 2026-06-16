/**
 * SettingsPanel — the settings screen, reskinned onto the design system (Phase C2). Theme switching
 * is the headline feature: selecting a theme calls `setTheme(id)` (the same global themeStore the Ink
 * UI uses → repaints every CSS var via {@link useThemeCssVars}) AND persists it through
 * `settings.update({ theme })` so it survives a reload. Pane gap, the input modifier, vim mode and the
 * collaborator harness are also surfaced (persisted via `settings.update`).
 *
 * ── THE LOCKED PANEL-REWRITE PATTERN (see TicketsPanel exemplar) ────────────────────────────────
 * Presentation moves onto DS primitives (Panel + form controls from the barrel); the data wiring is
 * UNCHANGED — same `s.settings` reads and `s.actions.settings.update`, and the theme control keeps
 * `useThemeId()` + `PALETTES` with the existing `chooseTheme` (setTheme for instant repaint, update to
 * persist). Each setting maps to its DS control: modifier → Radio, paneGap → numeric Input,
 * vimMode → Switch, collaborator harness → Select, theme → selectable swatch toggles (`data-on`).
 * Bespoke CSS lives in `styles/panels-settings.css` (wired in by the shell, not imported here).
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { PALETTES } from '@core/theme/palettes.js';
import type { ThemeId } from '@core/theme/palettes.js';
import { setTheme, useThemeId } from '@core/theme/themeStore.js';
import { Panel, Input, Select, Radio, Switch, cx } from '../ds/index.js';

const THEME_IDS = Object.keys(PALETTES) as ThemeId[];

const MODIFIER_OPTIONS = [
  { value: 'alt', label: 'alt' },
  { value: 'ctrl', label: 'ctrl' },
  { value: 'both', label: 'both' },
];

export function SettingsPanel(): React.JSX.Element {
  const settings = useAppStore((s) => s.settings, shallow);
  const update = useAppStore((s) => s.actions.settings.update);
  const activeTheme = useThemeId();

  const chooseTheme = (id: ThemeId): void => {
    setTheme(id); // instant repaint
    void update({ theme: id }); // persist
  };

  // The collaborator-harness override falls back to the daemon's live effective value when unset.
  const harnessValue = settings.collaboratorHarness ?? settings.effectiveCollaboratorHarness;
  const harnessOptions = Array.from(
    new Set([settings.effectiveCollaboratorHarness, ...settings.effectiveCrowHarnesses, harnessValue]),
  ).map((h) => ({ value: h, label: h }));

  return (
    <Panel title="settings">
      <div className="settings">
        <section className="settings__group">
          <h3 className="settings__heading">theme</h3>
          <div className="settings__themes">
            {THEME_IDS.map((id) => {
              const p = PALETTES[id];
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
                  <span className="theme-swatch__label">{id}</span>
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

        {settings.status === 'error' ? (
          <p className="settings__hint settings__hint--error">
            {settings.error ?? 'Failed to load settings.'}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
