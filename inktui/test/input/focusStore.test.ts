/**
 * focusStore tests — the re-home invariant (as a *derived* result), the derived candidate set, and
 * geometry-driven nav. The headline assertion: hiding the focused panel re-homes focus to chat
 * without any imperative re-home call — it falls out of {@link resolveFocus}.
 */

import { describe, expect, it } from 'vitest';
import {
  buildFocusGraph,
  navigateFocus,
  resolveEffectiveFocus,
} from '../../src/input/focusGraph.js';
import type { FocusId } from '../../src/input/focusStore.js';
import {
  CHAT_FOCUS,
  createFocusStore,
  isStagePaneId,
  type StagePaneId,
  selectEffectiveFocus,
} from '../../src/input/focusStore.js';
import type { Rect } from '../../src/input/geometry.js';
import { createPanelStore } from '../../src/input/panelStore.js';

const UNIT_RECT: Rect = { x: 0, y: 0, width: 1, height: 1 };

function rectsFor(ids: readonly FocusId[]): ReadonlyMap<FocusId, Rect> {
  return new Map(ids.map((id, index) => [id, { ...UNIT_RECT, x: index }]));
}

function resolveFocus(intended: FocusId, mountedIds: readonly FocusId[]): FocusId {
  return resolveEffectiveFocus(intended, buildFocusGraph({ rects: rectsFor(mountedIds) }));
}

function mountedStagePanesOf(rects: ReadonlyMap<FocusId, Rect>): Set<StagePaneId> {
  const panes = new Set<StagePaneId>();
  for (const id of rects.keys()) {
    if (isStagePaneId(id)) {
      panes.add(id);
    }
  }
  return panes;
}

function focusCandidates(rects: ReadonlyMap<FocusId, Rect>): readonly FocusId[] {
  const graph = buildFocusGraph({ rects });
  return [...graph.paneVertexIds.map((id) => id as FocusId), CHAT_FOCUS];
}

describe('resolveFocus (the re-home invariant, pure)', () => {
  it('keeps a visible panel focused', () => {
    expect(resolveFocus('plans', ['plans'])).toBe('plans');
  });

  it('re-homes a hidden panel to chat', () => {
    expect(resolveFocus('plans', [])).toBe(CHAT_FOCUS);
  });

  it('chat always resolves to itself', () => {
    expect(resolveFocus(CHAT_FOCUS, [])).toBe(CHAT_FOCUS);
  });

  it('keeps a mounted Stage pane focused', () => {
    const pane: StagePaneId = 'stage:chat:a1';
    expect(resolveFocus(pane, [pane])).toBe(pane);
  });

  it('re-homes an unmounted Stage pane to chat', () => {
    expect(resolveFocus('stage:chat:a1', [])).toBe(CHAT_FOCUS);
  });
});

describe('isStagePaneId / mountedStagePanesOf', () => {
  it('discriminates stage ids from panels + chat', () => {
    expect(isStagePaneId('stage:chat:a1')).toBe(true);
    expect(isStagePaneId('plans')).toBe(false);
    expect(isStagePaneId(CHAT_FOCUS)).toBe(false);
  });

  it('derives the mounted Stage panes from the rects map keys (panels + chat excluded)', () => {
    const r: Rect = { x: 0, y: 0, width: 1, height: 1 };
    const rects = new Map<FocusId, Rect>([
      ['plans', r],
      ['stage:chat:a1', r],
      [CHAT_FOCUS, r],
      ['stage:chat:b2', r],
    ]);
    expect([...mountedStagePanesOf(rects)]).toEqual(['stage:chat:a1', 'stage:chat:b2']);
  });
});

describe('focusCandidates (the derived candidate set)', () => {
  it('is the mounted pane vertices by geometry order, then chat', () => {
    expect(
      focusCandidates(
        new Map<FocusId, Rect>([
          ['tickets', { x: 20, y: 0, width: 1, height: 1 }],
          ['plans', { x: 0, y: 0, width: 1, height: 1 }],
          ['stage:chat:a1', { x: 40, y: 0, width: 1, height: 1 }],
        ]),
      ),
    ).toEqual(['plans', 'tickets', 'stage:chat:a1', CHAT_FOCUS]);
  });

  it('is just chat when nothing is visible/mounted — there is always somewhere to be', () => {
    expect(focusCandidates(new Map())).toEqual([CHAT_FOCUS]);
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
    focus.getState().measure('plans', UNIT_RECT);
    focus.getState().focus('plans');
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('re-homes to chat when the focused panel unmounts — no imperative call', () => {
    const panels = createPanelStore(['plans']);
    const focus = createFocusStore(panels);
    focus.getState().measure('plans', UNIT_RECT);
    focus.getState().focus('plans');
    expect(selectEffectiveFocus(focus)).toBe('plans');

    // The pane leaves the painted candidate set. Focus is never told to re-home.
    focus.getState().unmeasure('plans');

    // The invariant holds as a derived result: effective focus is chat.
    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
    // ...while the stored *intent* is untouched (re-show restores focus, proving it was derived).
    expect(focus.getState().intendedId).toBe('plans');
    focus.getState().measure('plans', UNIT_RECT);
    expect(selectEffectiveFocus(focus)).toBe('plans');
  });

  it('exactly one focusable is effective at all times across a toggle sequence', () => {
    const panels = createPanelStore();
    const focus = createFocusStore(panels);
    const oneFocused = () => {
      const eff = selectEffectiveFocus(focus);
      const candidates = focusCandidates(focus.getState().rects);
      // The effective focus is always present in the candidate set — never dangling.
      expect(candidates).toContain(eff);
    };
    oneFocused();
    panels.getState().toggle('plans');
    focus.getState().measure('plans', UNIT_RECT);
    focus.getState().focus('plans');
    oneFocused();
    panels.getState().toggle('crows');
    focus.getState().measure('crows', { ...UNIT_RECT, x: 1 });
    focus.getState().focus('crows');
    oneFocused();
    panels.getState().toggle('crows'); // hide the focused one
    focus.getState().unmeasure('crows');
    oneFocused();
  });
});

describe('focusStore — open pane history', () => {
  const plansRect: Rect = { x: 0, y: 0, width: 20, height: 4 };
  const ticketsRect: Rect = { x: 20, y: 0, width: 20, height: 4 };
  const stagePane: StagePaneId = 'stage:chat:a1';
  const stageRect: Rect = { x: 40, y: 0, width: 20, height: 4 };

  it('preserves first-open order across remeasure/reorder and appends reopened panes', () => {
    const panels = createPanelStore(['plans', 'tickets']);
    const focus = createFocusStore(panels);

    focus.getState().markPaneOpened('plans');
    focus.getState().measure('plans', plansRect);
    focus.getState().markPaneOpened('tickets');
    focus.getState().measure('tickets', ticketsRect);
    focus.getState().markPaneOpened(stagePane);
    focus.getState().measure(stagePane, stageRect);

    expect(focus.getState().graphState.openPaneIdsByOpenedAt).toEqual([
      'plans',
      'tickets',
      stagePane,
    ]);

    focus.getState().markPaneOpened('plans');
    focus.getState().measure('plans', { x: 80, y: 0, width: 20, height: 4 });
    focus.getState().measure('tickets', { x: 0, y: 0, width: 20, height: 4 });
    focus.getState().measure(stagePane, { x: 40, y: 0, width: 20, height: 4 });

    expect(focus.getState().graphState.openPaneIdsByOpenedAt).toEqual([
      'plans',
      'tickets',
      stagePane,
    ]);

    focus.getState().markPaneClosed('plans');
    focus.getState().unmeasure('plans');

    expect(focus.getState().graphState.openPaneIdsByOpenedAt).toEqual(['tickets', stagePane]);

    focus.getState().markPaneOpened('plans');
    focus.getState().measure('plans', plansRect);

    expect(focus.getState().graphState.openPaneIdsByOpenedAt).toEqual([
      'tickets',
      stagePane,
      'plans',
    ]);
  });

  it('does not admit chat into pane open history', () => {
    const panels = createPanelStore();
    const focus = createFocusStore(panels);

    focus.getState().markPaneOpened(CHAT_FOCUS);
    focus.getState().measure(CHAT_FOCUS, UNIT_RECT);

    expect(focus.getState().graphState.openPaneIdsByOpenedAt).toEqual([]);
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

describe('focusGraph — chat target partitions', () => {
  it('orders locked, ephemeral, and favorite-only virtual chat targets around the active target', () => {
    const graph = buildFocusGraph({
      rects: new Map<FocusId, Rect>([[CHAT_FOCUS, { x: 0, y: 0, width: 40, height: 3 }]]),
      chatTargets: {
        activeTargetId: 'agent-b',
        lockedVisibleTargetIds: ['agent-a'],
        ephemeralTargetId: 'agent-b',
        favoriteOnlyTargetIds: ['agent-c'],
      },
    });

    expect(graph.chatTargetVertexIds).toEqual([
      'chat:target:agent-a',
      'chat:target:agent-b',
      'chat:target:agent-c',
    ]);
    expect(graph.activeChatTargetVertexId).toBe('chat:target:agent-b');
    expect(navigateFocus(graph, CHAT_FOCUS, 'right').chatTargetId).toBe('agent-c');
    expect(navigateFocus(graph, CHAT_FOCUS, 'left').chatTargetId).toBe('agent-a');
  });
});

describe('focusStore — Stage panes (Phase 4a)', () => {
  // A left panel and a Stage chat pane to its right, chat below — the real Stage layout shape.
  const plansRect: Rect = { x: 0, y: 0, width: 20, height: 6 };
  const stagePane: StagePaneId = 'stage:chat:a1';
  const stageRect: Rect = { x: 20, y: 0, width: 30, height: 6 };
  const chatRect: Rect = { x: 0, y: 6, width: 50, height: 3 };

  function setup() {
    const panels = createPanelStore(['plans']);
    const focus = createFocusStore(panels);
    focus.getState().measure('plans', plansRect);
    focus.getState().measure(stagePane, stageRect);
    focus.getState().measure(CHAT_FOCUS, chatRect);
    return { panels, focus };
  }

  it('hjkl reaches a mounted Stage pane: right from a left panel lands on the chat pane', () => {
    const { focus } = setup();
    focus.getState().focus('plans');
    focus.getState().navigate('right');
    expect(selectEffectiveFocus(focus)).toBe(stagePane);
  });

  it('a mounted Stage pane holds focus (resolves to itself)', () => {
    const { focus } = setup();
    focus.getState().focus(stagePane);
    expect(selectEffectiveFocus(focus)).toBe(stagePane);
  });

  it('re-homes to chat when the focused Stage pane unmounts (unmeasure) — no imperative call', () => {
    const { focus } = setup();
    focus.getState().focus(stagePane);
    expect(selectEffectiveFocus(focus)).toBe(stagePane);

    // The pane leaves the tree: its measure-effect cleanup drops the rect. We never tell focus to
    // re-home — it falls out of resolveFocus, exactly like hiding a focused panel.
    focus.getState().unmeasure(stagePane);

    expect(selectEffectiveFocus(focus)).toBe(CHAT_FOCUS);
    // Intent is untouched (re-mount restores focus, proving it was derived).
    expect(focus.getState().intendedId).toBe(stagePane);
    focus.getState().measure(stagePane, stageRect);
    expect(selectEffectiveFocus(focus)).toBe(stagePane);
  });

  it('unmeasure is idempotent for an absent id (keeps map identity)', () => {
    const { focus } = setup();
    focus.getState().unmeasure(stagePane);
    const before = focus.getState().rects;
    focus.getState().unmeasure(stagePane);
    expect(focus.getState().rects).toBe(before);
  });
});
