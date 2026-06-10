/**
 * StdinShim tests — the byte-in/event-out wrapper around the real stdin. Covers: bypass passthrough
 * (behavior-neutral default), active-mode translation (legacy bytes forwarded, chords emitted), the
 * lone-ESC timeout flush through the shim's own timer, paste passthrough, detection-token routing to
 * a subscriber (and swallowing from downstream), and TTY-surface forwarding to the real stream.
 */

import { EventEmitter } from 'node:events';
import { describe, expect, it, vi } from 'vitest';
import type { CsiToken } from '../../src/terminal/csiU.js';
import { LONE_ESC_FLUSH_MS } from '../../src/terminal/csiU.js';
import { type RealStdin, StdinShim } from '../../src/terminal/StdinShim.js';
import type { Chord } from '../../src/terminal/translate.js';

/** A fake real-stdin: an EventEmitter with the TTY surface, recording control calls. */
class FakeStdin extends EventEmitter implements RealStdin {
  isTTY = true;
  rawMode: boolean | undefined;
  encoding: string | undefined;
  resumed = false;
  setRawMode(mode: boolean): this {
    this.rawMode = mode;
    return this;
  }
  setEncoding(enc: BufferEncoding): this {
    this.encoding = enc;
    return this;
  }
  resume(): this {
    this.resumed = true;
    return this;
  }
  pause(): this {
    return this;
  }
  /** Push a chunk as the real stdin would. */
  push(data: string | Buffer): void {
    this.emit('data', typeof data === 'string' ? Buffer.from(data, 'latin1') : data);
  }
}

/** Collect the downstream bytes the shim forwards, as latin1. Taps the synchronous `forward` event
 * (Ink reads via the async stream pull model; `forward` mirrors the same bytes deterministically). */
function collectDownstream(shim: StdinShim): { text(): string } {
  let buf = Buffer.alloc(0);
  shim.on('forward', (chunk: Buffer) => {
    buf = Buffer.concat([buf, chunk]);
  });
  return { text: () => buf.toString('latin1') };
}

describe('bypass mode (default) — behavior-neutral passthrough', () => {
  it('forwards bytes verbatim with no translation', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const down = collectDownstream(shim);
    real.push('hello');
    real.push('\x1b[A'); // an arrow key escape — verbatim in bypass
    expect(down.text()).toBe('hello\x1b[A');
    expect(shim.isBypass()).toBe(true);
  });

  it('mirrors isTTY from the real stream', () => {
    const real = new FakeStdin();
    real.isTTY = true;
    expect(new StdinShim(real).isTTY).toBe(true);
    const real2 = new FakeStdin();
    real2.isTTY = false;
    expect(new StdinShim(real2).isTTY).toBe(false);
  });
});

describe('active mode — translation', () => {
  it('forwards a legacy-representable kitty key as its legacy bytes', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const down = collectDownstream(shim);
    real.push('\x1b[115;5u'); // ctrl+s → legacy 0x13
    expect(down.text()).toBe('\x13');
  });

  it('emits a chord (not bytes) for ctrl+1', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const down = collectDownstream(shim);
    const chords: Chord[] = [];
    shim.on('chord', (c: Chord) => chords.push(c));
    real.push('\x1b[49;5u'); // ctrl+1
    expect(chords).toEqual([{ input: '1', ctrl: true, alt: false, shift: false }]);
    expect(down.text()).toBe(''); // no bytes downstream
  });

  it('passes a paste through verbatim in active mode', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setBypass(false);
    const down = collectDownstream(shim);
    real.push('pasted text 123');
    expect(down.text()).toBe('pasted text 123');
  });
});

describe('lone-ESC flush through the shim timer', () => {
  it('flushes a held ESC as a literal byte after LONE_ESC_FLUSH_MS', () => {
    vi.useFakeTimers();
    try {
      const real = new FakeStdin();
      const shim = new StdinShim(real);
      shim.setBypass(false);
      const down = collectDownstream(shim);
      real.push('\x1b'); // lone ESC — held, not yet downstream
      expect(down.text()).toBe('');
      vi.advanceTimersByTime(LONE_ESC_FLUSH_MS);
      expect(down.text()).toBe('\x1b');
    } finally {
      vi.useRealTimers();
    }
  });

  it('does NOT flush when the sequence completes before the timeout', () => {
    vi.useFakeTimers();
    try {
      const real = new FakeStdin();
      const shim = new StdinShim(real);
      shim.setBypass(false);
      const down = collectDownstream(shim);
      real.push('\x1b');
      real.push('[27u'); // completes esc before timer
      vi.advanceTimersByTime(LONE_ESC_FLUSH_MS);
      // esc translated to \x1b once; no double-flush.
      expect(down.text()).toBe('\x1b');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('detection token routing', () => {
  it('routes reply tokens to a subscriber and swallows them downstream (even in bypass)', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const down = collectDownstream(shim);
    const tokens: CsiToken[] = [];
    const unsubscribe = shim.subscribe((t) => tokens.push(t));
    real.push('\x1b[?1u'); // query reply
    real.push('\x1b[?62;c'); // DA1 reply
    expect(tokens).toEqual([{ type: 'queryReply', flags: 1 }, { type: 'daReply' }]);
    expect(down.text()).toBe(''); // never forwarded to Ink
    unsubscribe();
  });

  it('still forwards normal text downstream while a detection subscriber is active', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const down = collectDownstream(shim);
    shim.subscribe(() => {});
    real.push('abc');
    expect(down.text()).toBe('abc');
  });
});

describe('TTY surface forwarding', () => {
  it('forwards setRawMode / resume / setEncoding to the real stream', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    shim.setRawMode(true);
    shim.resume();
    shim.setEncoding('utf8');
    expect(real.rawMode).toBe(true);
    expect(real.resumed).toBe(true);
    expect(real.encoding).toBe('utf8');
  });

  it('dispose detaches from the real stream (no more forwarding)', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const down = collectDownstream(shim);
    shim.dispose();
    real.push('after dispose');
    expect(down.text()).toBe('');
  });
});
