/**
 * End-to-end terminal-path test (the plan's keystone): raw kitty bytes for ctrl+1 → StdinShim →
 * `chord` event → `chordToKey` → `dispatchKey` under a ctrl-modifier binding table → the
 * `focusPanel('plans')` global intent fires.
 *
 * This proves the whole Phase 2 chain links up without Ink/render: the bytes a kitty terminal sends
 * for ctrl+1 become the same dispatch decision the alt+1 path makes today (panel toggle), via the
 * side channel rather than a legacy byte (ctrl+digit has none).
 */

import { EventEmitter } from 'node:events';
import { describe, expect, it, vi } from 'vitest';
import { chordToKey } from '../../src/hooks/useRootInput.js';
import { resolveBindings } from '../../src/input/bindings.js';
import {
  type DispatchContext,
  dispatchKey,
  type GlobalHandlers,
} from '../../src/input/dispatcher.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import { type RealStdin, StdinShim } from '../../src/terminal/StdinShim.js';
import type { Chord } from '../../src/terminal/translate.js';

class FakeStdin extends EventEmitter implements RealStdin {
  isTTY = true;
  push(data: string): void {
    this.emit('data', Buffer.from(data, 'latin1'));
  }
}

describe('ctrl+1 raw bytes → shim → dispatch → focusPanel(plans)', () => {
  it('toggles the plans panel via the side-channel chord', () => {
    // 1. The shim, in active mode (protocol enabled), wrapping a fake stdin.
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const chords: Chord[] = [];
    shim.on('chord', (c: Chord) => chords.push(c));

    // 2. Raw kitty bytes for ctrl+1: CSI 49 ; 5 u (code 49='1', mods 5=ctrl).
    real.push('\x1b[49;5u');
    expect(chords).toHaveLength(1);

    // 3. Lift the chord to an Ink (input, key) — the exact conversion useRootInput performs.
    const chord = chords[0];
    if (chord === undefined) throw new Error('no chord');
    const { input, key } = chordToKey(chord);
    expect(input).toBe('1');
    expect(key.ctrl).toBe(true);
    expect(key.meta).toBe(false);

    // 4. Dispatch under a ctrl-modifier binding table (kitty available) and assert the intent.
    const focusPanel = vi.fn<GlobalHandlers['focusPanel']>();
    const handlers: GlobalHandlers = {
      focusPanel,
      navigate: vi.fn(),
      focusChat: vi.fn(),
      spawn: vi.fn(),
      toggleTmux: vi.fn(),
      newPlan: vi.fn(),
      newTicket: vi.fn(),
      openSettings: vi.fn(),
      keyHelp: vi.fn(),
      quickNote: vi.fn(),
      cycleTargetPrev: vi.fn(),
      cycleTargetNext: vi.fn(),
      toggleTargetPane: vi.fn(),
      murder: vi.fn(),
      murderPending: vi.fn(() => false),
      murderConfirm: vi.fn(),
      murderCancel: vi.fn(),
      closePane: vi.fn(),
    };
    const ctx: DispatchContext = {
      focusedId: CHAT_FOCUS,
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(focusPanel).toHaveBeenCalledWith('plans');
  });
});

describe('ctrl+j raw bytes → shim → dispatch → navigate(down)', () => {
  it('drives vim-nav down via the side-channel chord (byte 0x0a would be `enter` to Ink)', () => {
    // ctrl+j's legacy byte is 0x0a, which Ink's parser reports as `enter`/`return` — never
    // `{ctrl, input:'j'}` — so it must travel the side channel as a chord, exactly like ctrl+i/m/h.
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const chords: Chord[] = [];
    shim.on('chord', (c: Chord) => chords.push(c));

    // Raw kitty bytes for ctrl+j: CSI 106 ; 5 u (code 106='j', mods 5=ctrl).
    real.push('\x1b[106;5u');
    expect(chords).toHaveLength(1);

    // The chord carries the plain letter `j` (NOT a special-key name), so vim-nav can match it.
    const chord = chords[0];
    if (chord === undefined) throw new Error('no chord');
    expect(chord.input).toBe('j');
    const { input, key } = chordToKey(chord);
    expect(input).toBe('j');
    expect(key.ctrl).toBe(true);
    expect(key.meta).toBe(false);
    expect(key.return).toBe(false);

    const navigate = vi.fn<GlobalHandlers['navigate']>();
    const handlers: GlobalHandlers = {
      focusPanel: vi.fn(),
      navigate,
      focusChat: vi.fn(),
      spawn: vi.fn(),
      toggleTmux: vi.fn(),
      newPlan: vi.fn(),
      newTicket: vi.fn(),
      openSettings: vi.fn(),
      keyHelp: vi.fn(),
      quickNote: vi.fn(),
      cycleTargetPrev: vi.fn(),
      cycleTargetNext: vi.fn(),
      toggleTargetPane: vi.fn(),
      murder: vi.fn(),
      murderPending: vi.fn(() => false),
      murderConfirm: vi.fn(),
      murderCancel: vi.fn(),
      closePane: vi.fn(),
    };
    const ctx: DispatchContext = {
      focusedId: CHAT_FOCUS,
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(navigate).toHaveBeenCalledWith('down');
  });
});

describe('ctrl+h raw bytes → shim → dispatch (travel-left + cycleTargetPrev)', () => {
  /** Drive raw kitty bytes for ctrl+h through the shim and lift the resulting chord to an Ink
   * (input, key), exactly as useRootInput does. Returns the lifted event for the dispatcher. */
  function liftCtrlH(): { input: string; key: ReturnType<typeof chordToKey>['key'] } {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const chords: Chord[] = [];
    shim.on('chord', (c: Chord) => chords.push(c));

    // Raw kitty bytes for ctrl+h: CSI 104 ; 5 u (code 104='h', mods 5=ctrl). Ink would otherwise
    // report byte 0x08 as `backspace` — the chord restores the plain letter h.
    real.push('\x1b[104;5u');
    expect(chords).toHaveLength(1);
    const chord = chords[0];
    if (chord === undefined) throw new Error('no chord');
    expect(chord.input).toBe('h');

    const { input, key } = chordToKey(chord);
    expect(input).toBe('h');
    expect(key.ctrl).toBe(true);
    expect(key.meta).toBe(false);
    expect(key.backspace).toBe(false);
    return { input, key };
  }

  function makeHandlers(over: Partial<GlobalHandlers> = {}): GlobalHandlers {
    return {
      focusPanel: vi.fn(),
      navigate: vi.fn(),
      focusChat: vi.fn(),
      spawn: vi.fn(),
      toggleTmux: vi.fn(),
      newPlan: vi.fn(),
      newTicket: vi.fn(),
      openSettings: vi.fn(),
      keyHelp: vi.fn(),
      quickNote: vi.fn(),
      cycleTargetPrev: vi.fn(),
      cycleTargetNext: vi.fn(),
      toggleTargetPane: vi.fn(),
      murder: vi.fn(),
      murderPending: vi.fn(() => false),
      murderConfirm: vi.fn(),
      murderCancel: vi.fn(),
      closePane: vi.fn(),
      ...over,
    };
  }

  it('drives navigate(left) when a panel is focused (vim-nav left)', () => {
    const { input, key } = liftCtrlH();
    const navigate = vi.fn<GlobalHandlers['navigate']>();
    const handlers = makeHandlers({ navigate });
    const ctx: DispatchContext = {
      focusedId: 'plans',
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(navigate).toHaveBeenCalledWith('left');
    expect(handlers.cycleTargetPrev).not.toHaveBeenCalled();
  });

  it('drives cycleTargetPrev when chat is focused (super-chord)', () => {
    const { input, key } = liftCtrlH();
    const cycleTargetPrev = vi.fn<GlobalHandlers['cycleTargetPrev']>();
    const handlers = makeHandlers({ cycleTargetPrev });
    const ctx: DispatchContext = {
      focusedId: CHAT_FOCUS,
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(cycleTargetPrev).toHaveBeenCalledTimes(1);
    expect(handlers.navigate).not.toHaveBeenCalled();
  });
});

describe('ctrl+m raw bytes → shim → dispatch (murder arm + pending confirm)', () => {
  /** Drive raw kitty bytes for ctrl+m through the shim and lift the chord, exactly as useRootInput
   * does. ctrl+m is a CTRL_LETTER_COLLISION: the terminal conflates it with CR, so the chord names
   * the special key (`return`) and chordToKey lifts it to `{ ctrl: true, return: true }`. */
  function liftCtrlM(): { input: string; key: ReturnType<typeof chordToKey>['key'] } {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const chords: Chord[] = [];
    shim.on('chord', (c: Chord) => chords.push(c));

    // Raw kitty bytes for ctrl+m: CSI 109 ; 5 u (code 109='m', mods 5=ctrl).
    real.push('\x1b[109;5u');
    expect(chords).toHaveLength(1);
    const chord = chords[0];
    if (chord === undefined) throw new Error('no chord');
    expect(chord.input).toBe('return');

    const { input, key } = chordToKey(chord);
    expect(input).toBe('');
    expect(key.ctrl).toBe(true);
    expect(key.return).toBe(true);
    return { input, key };
  }

  function makeHandlers(over: Partial<GlobalHandlers> = {}): GlobalHandlers {
    return {
      focusPanel: vi.fn(),
      navigate: vi.fn(),
      focusChat: vi.fn(),
      spawn: vi.fn(),
      toggleTmux: vi.fn(),
      newPlan: vi.fn(),
      newTicket: vi.fn(),
      openSettings: vi.fn(),
      keyHelp: vi.fn(),
      quickNote: vi.fn(),
      cycleTargetPrev: vi.fn(),
      cycleTargetNext: vi.fn(),
      toggleTargetPane: vi.fn(),
      murder: vi.fn(),
      murderPending: vi.fn(() => false),
      murderConfirm: vi.fn(),
      murderCancel: vi.fn(),
      closePane: vi.fn(),
      ...over,
    };
  }

  it('arms the murder confirm from chat focus (the chord never types or sends)', () => {
    const { input, key } = liftCtrlM();
    const murder = vi.fn<GlobalHandlers['murder']>();
    const handlers = makeHandlers({ murder });
    const ctx: DispatchContext = {
      focusedId: CHAT_FOCUS,
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(murder).toHaveBeenCalledTimes(1);
  });

  it('confirms while pending (the second ctrl+m kills)', () => {
    const { input, key } = liftCtrlM();
    const murderConfirm = vi.fn<GlobalHandlers['murderConfirm']>();
    const handlers = makeHandlers({
      murderPending: vi.fn(() => true),
      murderConfirm,
    });
    const ctx: DispatchContext = {
      focusedId: CHAT_FOCUS,
      panelKeymaps: {},
      handlers,
      activeMode: null,
      bindings: resolveBindings('ctrl', true, {}),
    };
    const outcome = dispatchKey(input, key, ctx);

    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(murderConfirm).toHaveBeenCalledTimes(1);
    expect(handlers.murder).not.toHaveBeenCalled();
  });
});
