/**
 * kittyDriver tests — detection against a scripted fake stdio. The driver writes its query/DA1 and
 * resolves true/false based on which reply token the (fake) token source delivers, or false on
 * timeout. Enable/disable assert the wire sequences.
 */

import { describe, expect, it, vi } from 'vitest';
import type { CsiToken } from '../../src/terminal/csiU.js';
import {
  createKittyDriver,
  KITTY_ENABLE_FLAGS,
  type TokenSource,
} from '../../src/terminal/kittyDriver.js';

/** A fake token source whose `emit` pushes a token to all subscribers — the script's lever. */
function fakeTokens(): TokenSource & { emit(token: CsiToken): void } {
  const listeners = new Set<(t: CsiToken) => void>();
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    emit(token) {
      for (const l of listeners) l(token);
    },
  };
}

/** A writer that records every write. */
function fakeWriter(): { write(s: string): void; writes: string[] } {
  const writes: string[] = [];
  return { writes, write: (s) => writes.push(s) };
}

describe('detect', () => {
  it('writes the kitty query then DA1, in that order', async () => {
    const writer = fakeWriter();
    const tokens = fakeTokens();
    const driver = createKittyDriver(writer, tokens);
    const promise = driver.detect(50);
    expect(writer.writes).toEqual(['\x1b[?u', '\x1b[c']);
    // Resolve so the test doesn't dangle on the timer.
    tokens.emit({ type: 'daReply' });
    await promise;
  });

  it('resolves true when a kitty query reply arrives', async () => {
    const tokens = fakeTokens();
    const driver = createKittyDriver(fakeWriter(), tokens);
    const promise = driver.detect(50);
    tokens.emit({ type: 'queryReply', flags: 1 });
    expect(await promise).toBe(true);
  });

  it('resolves false when only a DA1 reply arrives (no kitty support)', async () => {
    const tokens = fakeTokens();
    const driver = createKittyDriver(fakeWriter(), tokens);
    const promise = driver.detect(50);
    tokens.emit({ type: 'daReply' });
    expect(await promise).toBe(false);
  });

  it('resolves false on timeout when nothing replies', async () => {
    vi.useFakeTimers();
    try {
      const tokens = fakeTokens();
      const driver = createKittyDriver(fakeWriter(), tokens);
      const promise = driver.detect(200);
      vi.advanceTimersByTime(200);
      expect(await promise).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores reply tokens after it has settled (unsubscribes)', async () => {
    const tokens = fakeTokens();
    const driver = createKittyDriver(fakeWriter(), tokens);
    const promise = driver.detect(50);
    tokens.emit({ type: 'queryReply', flags: 1 });
    expect(await promise).toBe(true);
    // A late DA1 must not throw or flip anything — the listener is gone.
    expect(() => tokens.emit({ type: 'daReply' })).not.toThrow();
  });
});

describe('enable / disable wire sequences', () => {
  it('enable pushes CSI > <flags> u', () => {
    const writer = fakeWriter();
    createKittyDriver(writer, fakeTokens()).enable();
    expect(writer.writes).toEqual([`\x1b[>${KITTY_ENABLE_FLAGS}u`]);
  });
  it('disable pops CSI < u', () => {
    const writer = fakeWriter();
    createKittyDriver(writer, fakeTokens()).disable();
    expect(writer.writes).toEqual(['\x1b[<u']);
  });
});
