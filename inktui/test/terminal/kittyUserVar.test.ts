/**
 * `kittyUserVar` tests — OSC 1337 SetUserVar emission guarded by `KITTY_WINDOW_ID`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ensureKittyMurderMarkerCleanup,
  setKittyUserVar,
} from '../../src/terminal/kittyUserVar.js';

describe('setKittyUserVar', () => {
  const originalWindowId = process.env.KITTY_WINDOW_ID;
  let writeSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    writeSpy = vi.spyOn(process.stdout, 'write').mockImplementation(() => true);
  });

  afterEach(() => {
    writeSpy.mockRestore();
    if (originalWindowId === undefined) {
      delete process.env.KITTY_WINDOW_ID;
    } else {
      process.env.KITTY_WINDOW_ID = originalWindowId;
    }
  });

  it('does nothing outside kitty', () => {
    delete process.env.KITTY_WINDOW_ID;
    setKittyUserVar('murder_tui', '1');
    expect(writeSpy).not.toHaveBeenCalled();
  });

  it('sets a base64-encoded value inside kitty', () => {
    process.env.KITTY_WINDOW_ID = '1';
    setKittyUserVar('murder_tui', '1');
    expect(writeSpy).toHaveBeenCalledWith('\x1b]1337;SetUserVar=murder_tui=MQ==\x07');
  });

  it('unsets by omitting the value inside kitty', () => {
    process.env.KITTY_WINDOW_ID = '1';
    setKittyUserVar('murder_tui', null);
    expect(writeSpy).toHaveBeenCalledWith('\x1b]1337;SetUserVar=murder_tui\x07');
  });
});

describe('ensureKittyMurderMarkerCleanup', () => {
  const originalWindowId = process.env.KITTY_WINDOW_ID;
  let writeSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    writeSpy = vi.spyOn(process.stdout, 'write').mockImplementation(() => true);
  });

  afterEach(() => {
    writeSpy.mockRestore();
    if (originalWindowId === undefined) {
      delete process.env.KITTY_WINDOW_ID;
    } else {
      process.env.KITTY_WINDOW_ID = originalWindowId;
    }
  });

  it('is a no-op outside kitty', () => {
    delete process.env.KITTY_WINDOW_ID;
    ensureKittyMurderMarkerCleanup();
    process.emit('exit');
    expect(writeSpy).not.toHaveBeenCalled();
  });

  it('unsets murder_tui on exit inside kitty', () => {
    process.env.KITTY_WINDOW_ID = '1';
    ensureKittyMurderMarkerCleanup();
    process.emit('exit');
    expect(writeSpy).toHaveBeenCalledWith('\x1b]1337;SetUserVar=murder_tui\x07');
  });
});
