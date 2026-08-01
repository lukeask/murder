/** PanelToggleStrip — selectTopBar labels; click toggles panelStore visibility. */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PANEL_IDS } from '@murder/ui-core/input/panels.js';
import { PanelToggleStrip } from '../src/components/PanelToggleStrip.js';
import { renderWithStore } from './helpers.js';

afterEach(cleanup);

describe('PanelToggleStrip', () => {
  it('renders digit-labeled panels and toggles visibility on click', () => {
    const { composer } = renderWithStore(<PanelToggleStrip />);
    const toolbar = screen.getByRole('toolbar', { name: 'Toggle panels' });
    expect(toolbar).toBeTruthy();

    const plans = screen.getByRole('button', { name: /plans₁/ });
    expect(plans.getAttribute('aria-pressed')).toBe('true');
    expect(composer.panels.getState().visible.has('plans')).toBe(true);

    fireEvent.click(plans);
    expect(composer.panels.getState().visible.has('plans')).toBe(false);
    expect(plans.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(plans);
    expect(composer.panels.getState().visible.has('plans')).toBe(true);

    for (const id of PANEL_IDS) {
      expect(screen.getByRole('button', { name: new RegExp(id) })).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: /crows₀/ })).toBeTruthy();
  });
});
