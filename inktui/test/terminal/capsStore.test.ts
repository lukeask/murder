/**
 * capsStore tests — the terminal-capability fact (kitty support). Starts in 'detecting', resolves to
 * a boolean once.
 */

import { describe, expect, it } from 'vitest';
import { createCapsStore } from '../../src/terminal/capsStore.js';

describe('capsStore', () => {
  it("starts in 'detecting' by default", () => {
    expect(createCapsStore().getState().kittySupported).toBe('detecting');
  });

  it('can be seeded (for tests)', () => {
    expect(createCapsStore(true).getState().kittySupported).toBe(true);
  });

  it('records a detection result', () => {
    const store = createCapsStore();
    store.getState().setKittySupported(false);
    expect(store.getState().kittySupported).toBe(false);
  });
});
