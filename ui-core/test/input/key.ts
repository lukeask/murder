/**
 * Test helper: build an Ink `Key` with all flags false, overriding the ones a test cares about.
 * Lets the pure keymap/dispatcher tests synthesise `(input, key)` events without rendering.
 */

import type { Key } from '@murder/ui-core/input/keymap.js';

export function makeKey(overrides: Partial<Key> = {}): Key {
  return {
    upArrow: false,
    downArrow: false,
    leftArrow: false,
    rightArrow: false,
    pageDown: false,
    pageUp: false,
    home: false,
    end: false,
    return: false,
    escape: false,
    ctrl: false,
    shift: false,
    tab: false,
    backspace: false,
    delete: false,
    meta: false,
    super: false,
    hyper: false,
    capsLock: false,
    numLock: false,
    ...overrides,
  };
}
