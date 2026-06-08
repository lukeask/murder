/**
 * focusStore tests — the re-home invariant (as a *derived* result), the derived candidate set, and
 * geometry-driven nav. The headline assertion: hiding the focused panel re-homes focus to chat
 * without any imperative re-home call — it falls out of {@link resolveFocus}.
 */

import { describe, expect, it } from 'vitest';
import {
  CHAT_FOCUS,
  createFocusStore,
  focusCandidates,
  resolveFocus,
  selectEffectiveFocus,
} from '../../src/input/focusStore.js';
import type { Rect } from '../../src/input/geometry.js';
import { createPanelStore } from '../../src/input/panelStore.js';

describe('resolveFocus (the re-home invariant, pure)', () => {
  it('keeps a visible panel focused', () => {
    expect(resolveFocus('plans', new Set(['plans']))).toBe('plans');
  });

  it('re-homes a hidden panel to chat', () => {
    expect(resolveFocus('plans', new Set())).toBe(CHAT_FOCUS);
  });

  it('chat always resolves to itself', () => {
    expect(resolveFocus(CHAT_FOCUS, new Set())).toBe(CHAT_FOCUS);
  });
});

describe('focusCandidates (the derived candidate set)', () => {
  it('is the visible panels in screen order, then chat', () => {
    expect(focusCandidates(new Set(['tickets', 'plans']))).toEqual([
      'plans',
      'tickets',
      CHAT_FOCUS,
    ]);
  });

  it('is just chat when no panel is visible — there is always somewhere to be', () => {
    expect(focusCandidates(new Set())).toEqual([CHAT_FOCUS]);
  });
});

describe('focusStore — effective focus & re-home', () => {
  it('starts focused on chat (always exactly one focusable, even at boot)', () => {
    const panels = createPanelStore();
    const focus = createFocusStore(panels);
    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
  });

  it('focuses a visible panel', () => {
    const panels = createPanelStore(['plans']);
    const focus = createFocusStore(panels);
    focus.getState().focus('plans');
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('re-homes to chat when the focused panel is hidden — no imperative call', () => {
    const panels = createPanelStore(['plans']);
    const focus = createFocusStore(panels);
    focus.getState().focus('plans');
    expect(selectEffectiveFocus(focus)).toBe('plans');

    // Hide the focused panel. We touch ONLY the panel store — focus is never told to re-home.
    panels.getState().hide('plans');

    // The invariant holds as a derived result: effective focus is chat.
    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
    // ...while the stored *intent* is untouched (re-show restores focus, proving it was derived).
    expect(focus.getState().intendedId).toBe('plans');
    panels.getState().show('plans');
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('exactly one focusable is effective at all times across a toggle sequence', () => {
    const panels = createPanelStore();
    const focus = createFocusStore(panels);
    const oneFocused = () => {
      const eff = selectEffectiveFocus(focus);
      const candidates = focusCandidates(panels.getState().visible);
      // The effective focus is always present in the candidate set — never dangling.
      expect(candidates).toContain(eff);
    };
    oneFocused();
    panels.getState().toggle('plans');
    focus.getState().focus('plans');
    oneFocused();
    panels.getState().toggle('crows');
    focus.getState().focus('crows');
    oneFocused();
    panels.getState().toggle('crows'); // hide the focused one
    oneFocused();
  });
});

describe('focusStore.navigate (geometry-driven)', () => {
  // plans (left) and tickets (right) side by side, chat below — like the real layout.
  const plansRect: Rect = { x: 0, y: 0, width: 20, height: 4 };
  const ticketsRect: Rect = { x: 20, y: 0, width: 20, height: 4 };
  const chatRect: Rect = { x: 0, y: 4, width: 40, height: 3 };

  function setup() {
    const panels = createPanelStore(['plans', 'tickets']);
    const focus = createFocusStore(panels);
    focus.getState().measure('plans', plansRect);
    focus.getState().measure('tickets', ticketsRect);
    focus.getState().measure(CHAT_FOCUS, chatRect);
    return { panels, focus };
  }

  it('moves right from plans to tickets', () => {
    const { focus } = setup();
    focus.getState().focus('plans');
    focus.getState().navigate('right');
    expect(selectEffectiveFocus(focus)).toBe('tickets');
  });

  it('moves left from tickets back to plans', () => {
    const { focus } = setup();
    focus.getState().focus('tickets');
    focus.getState().navigate('left');
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('moves down from a panel to chat', () => {
    const { focus } = setup();
    focus.getState().focus('plans');
    focus.getState().navigate('down');
    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
  });

  it('does not move at the layout edge', () => {
    const { focus } = setup();
    focus.getState().focus('tickets');
    focus.getState().navigate('right'); // nothing further right
    expect(selectEffectiveFocus(focus)).toBe('tickets');
  });

  it('measure dedupes an unchanged rect (keeps map identity)', () => {
    const { focus } = setup();
    const before = focus.getState().rects;
    focus.getState().measure('plans', plansRect);
    expect(focus.getState().rects).toBe(before);
  });
});
