/**
 * SettingsPanel — the settings screen. Theme switching is the headline feature: selecting a theme
 * calls `setTheme(id)` (the same global themeStore the Ink UI uses → repaints every CSS var via
 * {@link useThemeCssVars}) AND persists it through `settings.update({ theme })` so it survives a
 * reload. Pane gap and the input modifier are also surfaced (persisted via `settings.update`),
 * though the modifier only governs the optional desktop keyboard layer in the web port.
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { PALETTES } from '@core/theme/palettes.js';
import type { ThemeId } from '@core/theme/palettes.js';
import { setTheme, useThemeId } from '@core/theme/themeStore.js';
import { Panel } from '../Panel.js';

const THEME_IDS = Object.keys(PALETTES) as ThemeId[];

export function SettingsPanel(): React.JSX.Element {
  const settings = useAppStore((s) => s.settings, shallow);
  const update = useAppStore((s) => s.actions.settings.update);
  const activeTheme = useThemeId();

  const chooseTheme = (id: ThemeId): void => {
    setTheme(id); // instant repaint
    void update({ theme: id }); // persist
  };

  return (
    <Panel title="Settings">
      <div className="settings">
        <section className="settings__group">
          <h3>Theme</h3>
          <div className="settings__themes">
            {THEME_IDS.map((id) => (
              <button
                key={id}
                type="button"
                className="theme-swatch"
                data-on={id === activeTheme}
                onClick={() => chooseTheme(id)}
              >
                {id}
              </button>
            ))}
          </div>
        </section>

        <section className="settings__group">
          <h3>Pane gap</h3>
          <input
            type="range"
            min={0}
            max={4}
            value={settings.paneGap}
            onChange={(e) => void update({ pane_gap: Number(e.target.value) })}
          />
          <span className="settings__value">{settings.paneGap}</span>
        </section>

        <section className="settings__group">
          <h3>Keyboard modifier (desktop)</h3>
          <select
            value={settings.modifier}
            onChange={(e) => void update({ modifier: e.target.value as typeof settings.modifier })}
          >
            <option value="alt">alt</option>
            <option value="ctrl">ctrl</option>
            <option value="both">both</option>
          </select>
        </section>

        {settings.status === 'error' ? (
          <p className="panel__hint panel__hint--error">{settings.error ?? 'Failed to load settings.'}</p>
        ) : null}
      </div>
    </Panel>
  );
}
