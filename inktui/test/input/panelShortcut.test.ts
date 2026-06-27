import { describe, expect, it } from 'vitest';
import { togglePanelFromShortcut } from '../../src/hooks/useRootInput.js';
import { CHAT_FOCUS, createFocusStore, selectEffectiveFocus } from '../../src/input/focusStore.js';
import type { Rect } from '../../src/input/geometry.js';
import { createPanelStore } from '../../src/input/panelStore.js';

const UNIT_RECT: Rect = { x: 0, y: 0, width: 1, height: 1 };

describe('panel shortcut visibility/focus semantics', () => {
  it('shows a hidden panel and focuses it', () => {
    const panels = createPanelStore();
    const focus = createFocusStore(panels);

    togglePanelFromShortcut('plans', panels, focus);
    focus.getState().measure('plans', UNIT_RECT);

    expect(panels.getState().visible.has('plans')).toBe(true);
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('hides the focused panel and returns focus intent to chat', () => {
    const panels = createPanelStore(['plans']);
    const focus = createFocusStore(panels);
    focus.getState().measure('plans', UNIT_RECT);
    focus.getState().focus('plans');

    togglePanelFromShortcut('plans', panels, focus);

    expect(panels.getState().visible.has('plans')).toBe(false);
    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
    expect(focus.getState().intendedId).toBe(CHAT_FOCUS);
  });

  it('hides an unfocused panel without moving focus', () => {
    const panels = createPanelStore(['plans', 'notes']);
    const focus = createFocusStore(panels);
    focus.getState().measure('plans', UNIT_RECT);
    focus.getState().measure('notes', { ...UNIT_RECT, x: 1 });
    focus.getState().focus('notes');

    togglePanelFromShortcut('plans', panels, focus);

    expect(panels.getState().visible.has('plans')).toBe(false);
    expect(selectEffectiveFocus(focus)).toBe('notes');
    expect(focus.getState().intendedId).toBe('notes');
  });
});
