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
