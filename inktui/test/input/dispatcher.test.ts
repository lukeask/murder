/**
 * dispatcher tests — the layered dispatch decision, pure over synthesised key events. Covers the
 * three layers, the global chords (alt/meta-modified: the dispatcher routes on `key.meta + input`,
 * chosen over ctrl because terminals can't byte-encode Ctrl+digit but DO send Alt+<key> as an
 * ESC-prefixed `meta` event), and the rule that a panel's declared key fires only when focused.
 */

import { describe, expect, it, vi } from 'vitest';
import { resolveBindings } from '../../src/input/bindings.js';
import {
  type ChatInputHandler,
  type DispatchContext,
  dispatchKey,
  type GlobalHandlers,
} from '../../src/input/dispatcher.js';
import { CHAT_FOCUS, type FocusId } from '../../src/input/focusStore.js';
import { stageTranscriptFocusId } from '../../src/input/focusIds.js';
import type { PanelKeymap } from '../../src/input/keymap.js';
import type { Mode } from '../../src/input/modeStore.js';
import { makeKey } from './key.js';

/** The spy-handler bundle: each global handler is a `vi.fn` so call sites assert on it. Typed per
 * handler with its real signature so the spies are structurally the `GlobalHandlers` the dispatcher
 * wants — no cast needed. */
interface SpyHandlers {
  readonly focusPanel: ReturnType<typeof vi.fn<GlobalHandlers['focusPanel']>>;
  readonly navigate: ReturnType<typeof vi.fn<GlobalHandlers['navigate']>>;
  readonly focusChat: ReturnType<typeof vi.fn<GlobalHandlers['focusChat']>>;
  readonly spawn: ReturnType<typeof vi.fn<GlobalHandlers['spawn']>>;
  readonly cycleChatView: ReturnType<typeof vi.fn<GlobalHandlers['cycleChatView']>>;
  readonly newPlan: ReturnType<typeof vi.fn<GlobalHandlers['newPlan']>>;
  readonly newTicket: ReturnType<typeof vi.fn<GlobalHandlers['newTicket']>>;
  readonly openSettings: ReturnType<typeof vi.fn<GlobalHandlers['openSettings']>>;
  readonly quickNote: ReturnType<typeof vi.fn<GlobalHandlers['quickNote']>>;
  readonly keyHelp: ReturnType<typeof vi.fn<GlobalHandlers['keyHelp']>>;
  readonly cycleTargetPrev: ReturnType<typeof vi.fn<GlobalHandlers['cycleTargetPrev']>>;
  readonly cycleTargetNext: ReturnType<typeof vi.fn<GlobalHandlers['cycleTargetNext']>>;
  readonly toggleTargetGroup: ReturnType<
    typeof vi.fn<NonNullable<GlobalHandlers['toggleTargetGroup']>>
  >;
  readonly toggleTargetPane: ReturnType<typeof vi.fn<GlobalHandlers['toggleTargetPane']>>;
  readonly murder: ReturnType<typeof vi.fn<GlobalHandlers['murder']>>;
  readonly murderPending: ReturnType<typeof vi.fn<GlobalHandlers['murderPending']>>;
  readonly murderConfirm: ReturnType<typeof vi.fn<GlobalHandlers['murderConfirm']>>;
  readonly murderCancel: ReturnType<typeof vi.fn<GlobalHandlers['murderCancel']>>;
  readonly closePane: ReturnType<typeof vi.fn<GlobalHandlers['closePane']>>;
  readonly repaint: ReturnType<typeof vi.fn<GlobalHandlers['repaint']>>;
}

function handlers(): SpyHandlers {
  return {
    focusPanel: vi.fn<GlobalHandlers['focusPanel']>(),
    navigate: vi.fn<GlobalHandlers['navigate']>(),
    focusChat: vi.fn<GlobalHandlers['focusChat']>(),
    spawn: vi.fn<GlobalHandlers['spawn']>(),
    cycleChatView: vi.fn<GlobalHandlers['cycleChatView']>(),
    newPlan: vi.fn<GlobalHandlers['newPlan']>(),
    newTicket: vi.fn<GlobalHandlers['newTicket']>(),
    openSettings: vi.fn<GlobalHandlers['openSettings']>(),
    quickNote: vi.fn<GlobalHandlers['quickNote']>(),
    keyHelp: vi.fn<GlobalHandlers['keyHelp']>(),
    cycleTargetPrev: vi.fn<GlobalHandlers['cycleTargetPrev']>(),
    cycleTargetNext: vi.fn<GlobalHandlers['cycleTargetNext']>(),
    toggleTargetGroup: vi.fn<NonNullable<GlobalHandlers['toggleTargetGroup']>>(),
    toggleTargetPane: vi.fn<GlobalHandlers['toggleTargetPane']>(),
    murder: vi.fn<GlobalHandlers['murder']>(),
    murderPending: vi.fn<GlobalHandlers['murderPending']>(() => false),
    murderConfirm: vi.fn<GlobalHandlers['murderConfirm']>(),
    murderCancel: vi.fn<GlobalHandlers['murderCancel']>(),
    closePane: vi.fn<GlobalHandlers['closePane']>(),
    repaint: vi.fn<GlobalHandlers['repaint']>(),
  };
}

function ctx(
  focusedId: FocusId,
  h: SpyHandlers,
  panelKeymaps: DispatchContext['panelKeymaps'] = {},
  activeMode: DispatchContext['activeMode'] = null,
  bindings?: DispatchContext['bindings'],
): DispatchContext {
  // Omitting `bindings` exercises the DEFAULT_BINDINGS fallback (today's alt behavior) — the
  // zero-behavior-change guarantee for existing call sites.
  return { focusedId, handlers: h, panelKeymaps, activeMode, ...(bindings ? { bindings } : {}) };
}

describe('layer 0 — active-mode capture', () => {
  const onModeIntent = vi.fn();
  /** A mode that declares `y`→confirm and Esc→dismiss; capture-everything (no pass-through). */
  function captureMode(passThrough = false): Mode {
    return {
      id: 'm',
      presentation: 'modal',
      passThrough,
      keymap: [
        { chord: { input: 'y' }, intent: 'confirm', description: 'yes' },
        { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
      ],
      onIntent: onModeIntent,
      render: () => null,
    };
  }

  it('routes a matching key to the active mode (handled), nothing below fires', () => {
    onModeIntent.mockClear();
    const h = handlers();
    const out = dispatchKey('y', makeKey(), ctx('tickets', h, {}, captureMode()));
    expect(onModeIntent).toHaveBeenCalledWith('confirm');
    expect(out).toEqual({ layer: 'mode', handled: true });
    expect(h.focusPanel).not.toHaveBeenCalled();
  });

  it('matches a special-key chord (Esc) — dismiss is just a declared chord', () => {
    onModeIntent.mockClear();
    const out = dispatchKey(
      '',
      makeKey({ escape: true }),
      ctx('chat', handlers(), {}, captureMode()),
    );
    expect(onModeIntent).toHaveBeenCalledWith('dismiss');
    expect(out).toEqual({ layer: 'mode', handled: true });
  });

  it('SWALLOWS an unmatched key (no pass-through) — global chords suppressed under the modal', () => {
    onModeIntent.mockClear();
    const h = handlers();
    // alt+1 would normally focus a panel; under a capturing modal it must NOT.
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, captureMode()));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(onModeIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'mode', handled: false });
  });

  it('falls through to lower layers when the mode declares pass-through', () => {
    onModeIntent.mockClear();
    const h = handlers();
    // alt+1 unmatched by the mode, but pass-through is on → layer 1 fires.
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, captureMode(true)));
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('calls onUncaptured for unmatched keys when present — returns handled:true if consumed', () => {
    onModeIntent.mockClear();
    const consumed: string[] = [];
    const modeWithUncaptured: Mode = {
      ...captureMode(),
      onUncaptured(input) {
        if (input.length > 0 && input !== '\x01') {
          consumed.push(input);
          return true;
        }
        return false;
      },
    };
    const h = handlers();
    // 'a' is not in the mode keymap, but onUncaptured returns true → mode consumed it.
    const out = dispatchKey('a', makeKey(), ctx('tickets', h, {}, modeWithUncaptured));
    expect(consumed).toEqual(['a']);
    expect(out).toEqual({ layer: 'mode', handled: true });
    expect(h.focusPanel).not.toHaveBeenCalled();
  });

  it('onUncaptured returning false still swallows when passThrough is off', () => {
    onModeIntent.mockClear();
    const modeWithUncaptured: Mode = {
      ...captureMode(),
      onUncaptured(_input) {
        return false; // not consumed
      },
    };
    const h = handlers();
    // alt+1 unmatched; onUncaptured returns false; no pass-through → swallow (handled:false).
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, modeWithUncaptured));
    expect(h.focusPanel).not.toHaveBeenCalled(); // global chord suppressed by modal
    expect(out).toEqual({ layer: 'mode', handled: false });
  });
});

describe('layer 1 — global chords', () => {
  it('alt+<n> focuses the mapped panel (1 → plans), even while chat is focused', () => {
    const h = handlers();
    const out = dispatchKey('1', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+0 maps to crows (right region, screen-position mapping)', () => {
    const h = handlers();
    dispatchKey('0', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).toHaveBeenCalledWith('crows');
  });

  it('alt+5 toggles the history panel', () => {
    const h = handlers();
    dispatchKey('5', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).toHaveBeenCalledWith('history');
  });

  it('a reserved digit (alt+6) is a no-op', () => {
    const h = handlers();
    const out = dispatchKey('6', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat'); // falls through to the chat short-circuit
  });

  it('alt+h/j/k/l navigate', () => {
    const h = handlers();
    dispatchKey('h', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('j', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('k', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('l', makeKey({ meta: true }), ctx('plans', h));
    expect(h.navigate.mock.calls.map((c) => c[0])).toEqual(['left', 'down', 'up', 'right']);
  });

  it('alt+space focuses chat; alt+y is freed/parked (no longer a chord)', () => {
    const h = handlers();
    dispatchKey(' ', makeKey({ meta: true }), ctx('plans', h));
    // TUIchat-3: `y` was the old tmux chord; it is now freed/parked, so alt+y fires nothing global
    // (and the fullscreen tmux mode it drove was retired in TUIchat-5). alt+y falls through unhandled.
    const out = dispatchKey('y', makeKey({ meta: true }), ctx('plans', h));
    expect(h.focusChat).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('alt+f does NOT claim at the global layer — it falls through to the focused panel keymap', () => {
    const h = handlers();
    const onIntent = vi.fn();
    // A panel that declares alt+f → star (the generalized favorite/star pattern).
    const starKeymap: PanelKeymap = {
      keymap: [
        { chord: { input: 'f', key: { meta: true } }, intent: 'star', description: 'favorite' },
      ],
      onIntent,
    };
    const out = dispatchKey('f', makeKey({ meta: true }), ctx('plans', h, { plans: starKeymap }));
    expect(h.focusChat).not.toHaveBeenCalled(); // global layer declined alt+f
    expect(onIntent).toHaveBeenCalledWith('star'); // layer 3 (panel keymap) handled it
    expect(out).toEqual({ layer: 'panel', handled: true });
  });

  it('alt+s spawns ONLY when chat is focused (C11 dual-purpose chord)', () => {
    const h = handlers();
    const out = dispatchKey('s', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+s does NOT spawn when a list panel is focused — it falls through (panels no longer use alt+s)', () => {
    const h = handlers();
    // Panels no longer declare an alt+s binding; the global layer declines and it falls through to
    // layer 3, where the focused panel declares nothing for it → unhandled.
    const out = dispatchKey('s', makeKey({ meta: true }), ctx('plans', h, {}));
    expect(h.spawn).not.toHaveBeenCalled(); // global layer declined alt+s (not chat/Stage focus)
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('alt+s spawns when a transcript-history Stage pane is highlighted (stagelayout)', () => {
    const h = handlers();
    const out = dispatchKey('s', makeKey({ meta: true }), ctx(stageTranscriptFocusId('crow-1'), h));
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+s spawns when the open doc Stage pane is highlighted (stagelayout)', () => {
    const h = handlers();
    const out = dispatchKey('s', makeKey({ meta: true }), ctx('stage:doc:my-plan', h));
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+p fires newPlan (C12 new-plan chord)', () => {
    const h = handlers();
    const out = dispatchKey('p', makeKey({ meta: true }), ctx('plans', h));
    expect(h.newPlan).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+t fires cycleChatView (TUIchat-3 — took over `t` from the now chord-less newTicket)', () => {
    const h = handlers();
    const out = dispatchKey('t', makeKey({ meta: true }), ctx('plans', h));
    expect(h.cycleChatView).toHaveBeenCalledOnce();
    expect(h.newTicket).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+o fires openSettings (Phase 5 settings chord)', () => {
    const h = handlers();
    const out = dispatchKey('o', makeKey({ meta: true }), ctx('plans', h));
    expect(h.openSettings).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+o fires openSettings even while chat is focused (app-wide chord)', () => {
    const h = handlers();
    const out = dispatchKey('o', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.openSettings).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('a plain (non-alt) char is not a global chord', () => {
    const h = handlers();
    const out = dispatchKey('1', makeKey({ meta: false }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });

  it('ctrl+n fires quickNote (plain chord, app-wide) under the default alt modifier', () => {
    const h = handlers();
    const out = dispatchKey('n', makeKey({ ctrl: true }), ctx('plans', h));
    expect(h.quickNote).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+n fires quickNote even while chat is focused', () => {
    const h = handlers();
    const out = dispatchKey('n', makeKey({ ctrl: true }), ctx(CHAT_FOCUS, h));
    expect(h.quickNote).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+r fires repaint (plain chord, app-wide) under the default alt modifier', () => {
    const h = handlers();
    const out = dispatchKey('r', makeKey({ ctrl: true }), ctx('plans', h));
    expect(h.repaint).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+r fires repaint even while chat is focused', () => {
    const h = handlers();
    const out = dispatchKey('r', makeKey({ ctrl: true }), ctx(CHAT_FOCUS, h));
    expect(h.repaint).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });
  it('plain ? fires keyHelp when a panel is focused (item 12, no modifier needed)', () => {
    const h = handlers();
    const out = dispatchKey('?', makeKey(), ctx('plans', h));
    expect(h.keyHelp).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('plain ? does NOT fire keyHelp while chat is focused (falls to the input as a literal)', () => {
    const h = handlers();
    const out = dispatchKey('?', makeKey(), ctx(CHAT_FOCUS, h));
    expect(h.keyHelp).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });
});

describe('global.closePane — ctrl+q closes the highlighted Stage pane (stagelayout)', () => {
  // ctrl+q is a plain chord delivered as the clean legacy byte → `{ ctrl: true, input: 'q' }`.
  const CTRL_Q = makeKey({ ctrl: true });

  it('ctrl+q closes a highlighted transcript-history Stage pane (claimed at the global layer)', () => {
    const h = handlers();
    const out = dispatchKey('q', CTRL_Q, ctx(stageTranscriptFocusId('crow-1'), h));
    expect(h.closePane).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+q closes a highlighted doc Stage pane', () => {
    const h = handlers();
    const out = dispatchKey('q', CTRL_Q, ctx('stage:doc:my-plan', h));
    expect(h.closePane).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+q does NOTHING when chat is focused (falls through to the chat short-circuit)', () => {
    const h = handlers();
    const out = dispatchKey('q', CTRL_Q, ctx(CHAT_FOCUS, h));
    expect(h.closePane).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });

  it('ctrl+q does NOTHING when a list panel is focused (falls through to the panel keymap)', () => {
    const h = handlers();
    const out = dispatchKey('q', CTRL_Q, ctx('plans', h, {}));
    expect(h.closePane).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('ctrl+q fires close-pane under modifier=ctrl too (plain chord, not shadowed by the gate)', () => {
    const h = handlers();
    const ctrlBindings = resolveBindings('ctrl', true, {});
    const out = dispatchKey('q', CTRL_Q, ctx('stage:doc:my-plan', h, {}, null, ctrlBindings));
    expect(h.closePane).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });
});

describe('layer 1 — chat-target super-chords (item 9)', () => {
  it('ctrl+j toggles the chat target group under the default alt modifier', () => {
    const h = handlers();
    const out = dispatchKey('j', makeKey({ ctrl: true }), ctx(CHAT_FOCUS, h));
    expect(h.toggleTargetGroup).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it('ctrl+j does not toggle the chat target group away from chat focus', () => {
    const h = handlers();
    const out = dispatchKey('j', makeKey({ ctrl: true }), ctx('plans', h));
    expect(h.toggleTargetGroup).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('alt+h / alt+l cycle the chat target while chat is focused', () => {
    const h = handlers();
    const prev = dispatchKey('h', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    const next = dispatchKey('l', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.cycleTargetPrev).toHaveBeenCalledOnce();
    expect(h.cycleTargetNext).toHaveBeenCalledOnce();
    expect(prev).toEqual({ layer: 'global', handled: true });
    expect(next).toEqual({ layer: 'global', handled: true });
    // The geometric nav must NOT fire for h/l while chat is focused (the cycle chords preempt it).
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it('alt+w toggles the target pane while chat is focused', () => {
    const h = handlers();
    const out = dispatchKey('w', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.toggleTargetPane).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+h is geometric nav (NOT target cycling) when a panel is focused', () => {
    const h = handlers();
    dispatchKey('h', makeKey({ meta: true }), ctx('plans', h));
    expect(h.navigate).toHaveBeenCalledWith('left');
    expect(h.cycleTargetPrev).not.toHaveBeenCalled();
  });

  it('alt+w is unbound (no-op) when a panel is focused', () => {
    const h = handlers();
    const out = dispatchKey('w', makeKey({ meta: true }), ctx('plans', h));
    expect(h.toggleTargetPane).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });
});

describe('layer 2 — chat short-circuit', () => {
  it('yields a non-chord event to the input when chat is focused (handled=false)', () => {
    const h = handlers();
    const out = dispatchKey('x', makeKey(), ctx(CHAT_FOCUS, h));
    expect(out).toEqual({ layer: 'chat', handled: false });
  });
});

describe('layer 3 — focused panel keymap', () => {
  const onIntent = vi.fn();
  const plansKeymap: PanelKeymap = {
    keymap: [{ chord: { input: 'a' }, intent: 'act', description: 'act' }],
    onIntent,
  };

  it('fires the panel intent when that panel is focused', () => {
    onIntent.mockClear();
    const out = dispatchKey('a', makeKey(), ctx('plans', handlers(), { plans: plansKeymap }));
    expect(onIntent).toHaveBeenCalledWith('act');
    expect(out).toEqual({ layer: 'panel', handled: true });
  });

  it('does NOT fire the panel intent when a different panel is focused', () => {
    onIntent.mockClear();
    // 'a' is plans' key, but tickets is focused (and declares nothing) → no intent.
    const out = dispatchKey('a', makeKey(), ctx('tickets', handlers(), { plans: plansKeymap }));
    expect(onIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('ignores a key the focused panel did not declare', () => {
    onIntent.mockClear();
    const out = dispatchKey('z', makeKey(), ctx('plans', handlers(), { plans: plansKeymap }));
    expect(onIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });
});

describe('command modifier — ctrl', () => {
  const ctrlBindings = resolveBindings('ctrl', true, {});

  it('ctrl+<n> focuses the mapped panel (digit gate honours ctrl)', () => {
    const h = handlers();
    const out = dispatchKey(
      '1',
      makeKey({ ctrl: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+t cycles the chat view, ctrl+space focuses chat (TUIchat-3)', () => {
    const h = handlers();
    dispatchKey('t', makeKey({ ctrl: true }), ctx('plans', h, {}, null, ctrlBindings));
    dispatchKey(' ', makeKey({ ctrl: true }), ctx('plans', h, {}, null, ctrlBindings));
    expect(h.cycleChatView).toHaveBeenCalledOnce();
    expect(h.focusChat).toHaveBeenCalledOnce();
  });

  it('ctrl+s spawns only when chat is focused', () => {
    const h = handlers();
    const chatOut = dispatchKey(
      's',
      makeKey({ ctrl: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(chatOut).toEqual({ layer: 'global', handled: true });
    const panelOut = dispatchKey(
      's',
      makeKey({ ctrl: true }),
      ctx('plans', h, {}, null, ctrlBindings),
    );
    expect(panelOut).toEqual({ layer: 'panel', handled: false });
  });

  it('ctrl+n fires quickNote under modifier=ctrl (plain chord, NOT shadowed by the command gate)', () => {
    // Regression guard for item 10: under modifier=ctrl, `isCommandModified` is true for ctrl+n, so a
    // naive gate-first order would route it into the digit/named-command branch (no match → swallowed).
    // The dispatcher matches the plain quickNote chord BEFORE the gate, so it still reaches the handler.
    const h = handlers();
    const out = dispatchKey('n', makeKey({ ctrl: true }), ctx('plans', h, {}, null, ctrlBindings));
    expect(h.quickNote).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+<n> is NOT a command chord under ctrl-only (degraded to chat short-circuit)', () => {
    const h = handlers();
    const out = dispatchKey(
      '1',
      makeKey({ meta: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });

  it('a panel-resolved ctrl+f stars (panel keymap built from the same bindings)', () => {
    const h = handlers();
    const onIntent = vi.fn();
    const starKeymap: PanelKeymap = {
      keymap: [{ chord: ctrlBindings.chordsFor('panel.star'), intent: 'star', description: 'fav' }],
      onIntent,
    };
    const out = dispatchKey(
      'f',
      makeKey({ ctrl: true }),
      ctx('plans', h, { plans: starKeymap }, null, ctrlBindings),
    );
    expect(onIntent).toHaveBeenCalledWith('star');
    expect(out).toEqual({ layer: 'panel', handled: true });
  });
});

describe('command modifier — both (alt still works)', () => {
  const bothBindings = resolveBindings('both', true, {});

  it('alt+<n> AND ctrl+<n> both focus the panel', () => {
    const h = handlers();
    dispatchKey('1', makeKey({ meta: true }), ctx(CHAT_FOCUS, h, {}, null, bothBindings));
    dispatchKey('2', makeKey({ ctrl: true }), ctx(CHAT_FOCUS, h, {}, null, bothBindings));
    expect(h.focusPanel.mock.calls.map((c) => c[0])).toEqual(['plans', 'notes']);
  });

  it('alt+t and ctrl+t both cycle the chat view (TUIchat-3)', () => {
    const h = handlers();
    dispatchKey('t', makeKey({ meta: true }), ctx('plans', h, {}, null, bothBindings));
    dispatchKey('t', makeKey({ ctrl: true }), ctx('plans', h, {}, null, bothBindings));
    expect(h.cycleChatView).toHaveBeenCalledTimes(2);
  });
});

describe('global.murder — ctrl+m arm + the pending confirm check', () => {
  // ctrl+m rides the kitty side-channel as `chord { input: 'return', ctrl: true }`, lifted by
  // chordToKey to an empty input with `{ ctrl, return }` flags — synthesise exactly that.
  const CTRL_M = makeKey({ ctrl: true, return: true });

  it('ctrl+m fires murder (arm) from chat focus', () => {
    const h = handlers();
    const out = dispatchKey('', CTRL_M, ctx(CHAT_FOCUS, h));
    expect(out).toEqual({ layer: 'global', handled: true });
    expect(h.murder).toHaveBeenCalledTimes(1);
    expect(h.murderConfirm).not.toHaveBeenCalled();
  });

  it('ctrl+m never reaches the chat input (plain Enter still does)', () => {
    const h = handlers();
    const handleKey = vi.fn<ChatInputHandler['handleKey']>(() => true);
    const context: DispatchContext = { ...ctx(CHAT_FOCUS, h), chatInput: { handleKey } };
    dispatchKey('', CTRL_M, context);
    expect(handleKey).not.toHaveBeenCalled();
    // Plain Enter (no ctrl) is NOT the murder chord — it belongs to the chat field.
    dispatchKey('', makeKey({ return: true }), context);
    expect(h.murder).toHaveBeenCalledTimes(1);
    expect(handleKey).toHaveBeenCalledTimes(1);
  });

  it('ctrl+m DECLINES with the crows panel focused (falls through to the panel keymap)', () => {
    const h = handlers();
    const onIntent = vi.fn();
    const keymap = {
      keymap: [
        {
          chord: { key: { ctrl: true, return: true } },
          intent: 'murder',
          description: 'murder',
        },
      ],
      onIntent,
    };
    const out = dispatchKey('', CTRL_M, ctx('crows', h, { crows: keymap }));
    expect(h.murder).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: true });
    expect(onIntent).toHaveBeenCalledWith('murder');
  });

  it('while pending, a plain m confirms (claimed ahead of chat typing)', () => {
    const h = handlers();
    h.murderPending.mockReturnValue(true);
    const handleKey = vi.fn<ChatInputHandler['handleKey']>(() => true);
    const context: DispatchContext = { ...ctx(CHAT_FOCUS, h), chatInput: { handleKey } };
    const out = dispatchKey('m', makeKey(), context);
    expect(out).toEqual({ layer: 'global', handled: true });
    expect(h.murderConfirm).toHaveBeenCalledTimes(1);
    expect(handleKey).not.toHaveBeenCalled(); // the confirm m is never typed
  });

  it('while pending, ctrl+m again confirms', () => {
    const h = handlers();
    h.murderPending.mockReturnValue(true);
    const out = dispatchKey('', CTRL_M, ctx(CHAT_FOCUS, h));
    expect(out).toEqual({ layer: 'global', handled: true });
    expect(h.murderConfirm).toHaveBeenCalledTimes(1);
    expect(h.murder).not.toHaveBeenCalled(); // confirm, not a re-arm
  });

  it('while pending, any other key cancels WITHOUT being consumed (it keeps its meaning)', () => {
    const h = handlers();
    h.murderPending.mockReturnValue(true);
    const handleKey = vi.fn<ChatInputHandler['handleKey']>(() => true);
    const context: DispatchContext = { ...ctx(CHAT_FOCUS, h), chatInput: { handleKey } };
    const out = dispatchKey('x', makeKey(), context);
    expect(h.murderCancel).toHaveBeenCalledTimes(1);
    expect(h.murderConfirm).not.toHaveBeenCalled();
    // The x still types into the chat field — cancel does not swallow the event.
    expect(handleKey).toHaveBeenCalledWith('x', expect.anything());
    expect(out).toEqual({ layer: 'chat', handled: true });
  });

  it('while pending, alt+m (meta) is NOT the confirm — it cancels and falls through', () => {
    const h = handlers();
    h.murderPending.mockReturnValue(true);
    dispatchKey('m', makeKey({ meta: true }), ctx('tickets', h));
    expect(h.murderConfirm).not.toHaveBeenCalled();
    expect(h.murderCancel).toHaveBeenCalledTimes(1);
  });

  it('not pending: a plain m is ordinary typing/panel input (no murder handlers fire)', () => {
    const h = handlers();
    const out = dispatchKey('m', makeKey(), ctx(CHAT_FOCUS, h));
    expect(h.murder).not.toHaveBeenCalled();
    expect(h.murderConfirm).not.toHaveBeenCalled();
    expect(h.murderCancel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });
});
