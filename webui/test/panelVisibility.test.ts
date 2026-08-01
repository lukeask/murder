/**
 * Panel visibility toggle — digit chords hide/show rail panels via panelStore.
 */

import { describe, expect, it } from 'vitest';
import { createPanelStore } from '@murder/ui-core/input/panelStore.js';
import { PANEL_IDS } from '@murder/ui-core/input/panels.js';
import { togglePanelVisibility } from '../src/panelVisibility.js';
import { createComposerStores } from '../src/composer/createComposerStores.js';
import { panelFocusStore } from '../src/panelFocus.js';

describe('panel visibility', () => {
  it('createComposerStores seeds every rail panel visible', () => {
    const stores = createComposerStores();
    for (const id of PANEL_IDS) {
      expect(stores.panels.getState().visible.has(id)).toBe(true);
    }
  });

  it('togglePanelVisibility hides then shows', () => {
    const panels = createPanelStore(PANEL_IDS);
    expect(panels.getState().visible.has('plans')).toBe(true);
    togglePanelVisibility(panels, 'plans');
    expect(panels.getState().visible.has('plans')).toBe(false);
    togglePanelVisibility(panels, 'plans');
    expect(panels.getState().visible.has('plans')).toBe(true);
  });

  it('showing a panel focuses it for keyboard nav', () => {
    const panels = createPanelStore(PANEL_IDS);
    panels.getState().hide('history');
    togglePanelVisibility(panels, 'history');
    expect(panelFocusStore.getState().focusedId).toBe('history');
    togglePanelVisibility(panels, 'history');
    expect(panelFocusStore.getState().focusedId).toBeNull();
  });
});
