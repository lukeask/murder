/**
 * `canvasBackground` tests — Kitty OSC 21 push/set/pop for Background Transparency.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  _resetCanvasBackgroundForTests,
  applyCanvasBackground,
  resolveBackgroundTransparency,
  restoreCanvasBackground,
  setBackgroundTransparencyPreview,
} from '../../src/terminal/canvasBackground.js';

describe('canvasBackground', () => {
  const originalWindowId = process.env['KITTY_WINDOW_ID'];
  let writeSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    _resetCanvasBackgroundForTests();
    writeSpy = vi.spyOn(process.stdout, 'write').mockImplementation(() => true);
  });

  afterEach(() => {
    writeSpy.mockRestore();
    _resetCanvasBackgroundForTests();
    if (originalWindowId === undefined) {
      delete process.env['KITTY_WINDOW_ID'];
    } else {
      process.env['KITTY_WINDOW_ID'] = originalWindowId;
    }
  });

  it('does nothing outside kitty when applying a translucent canvas', () => {
    delete process.env['KITTY_WINDOW_ID'];
    applyCanvasBackground(50, '#272e33');
    expect(writeSpy).not.toHaveBeenCalled();
  });

  it('pushes the color stack and sets background + transparent slot inside kitty', () => {
    process.env['KITTY_WINDOW_ID'] = '1';
    applyCanvasBackground(50, '#272e33');
    expect(writeSpy).toHaveBeenCalledWith('\x1b]30001\x1b\\');
    expect(writeSpy).toHaveBeenCalledWith(
      '\x1b]21;background=#272e33;transparent_background_color1=#272e33@0.5\x1b\\',
    );
  });

  it('maps transparency 0 to fully opaque cell opacity', () => {
    process.env['KITTY_WINDOW_ID'] = '1';
    applyCanvasBackground(0, '#272e33');
    expect(writeSpy).toHaveBeenCalledWith(
      '\x1b]21;background=#272e33;transparent_background_color1=#272e33@1\x1b\\',
    );
  });

  it('pops the color stack when restoring to 100% after a prior apply', () => {
    process.env['KITTY_WINDOW_ID'] = '1';
    applyCanvasBackground(50, '#272e33');
    writeSpy.mockClear();
    applyCanvasBackground(100, '#272e33');
    expect(writeSpy).toHaveBeenCalledWith('\x1b]30101\x1b\\');
  });

  it('restoreCanvasBackground is a no-op when nothing was pushed', () => {
    process.env['KITTY_WINDOW_ID'] = '1';
    restoreCanvasBackground();
    expect(writeSpy).not.toHaveBeenCalled();
  });

  it('skips redundant re-applies of the same transparency and hex', () => {
    process.env['KITTY_WINDOW_ID'] = '1';
    applyCanvasBackground(25, '#272e33');
    writeSpy.mockClear();
    applyCanvasBackground(25, '#272e33');
    expect(writeSpy).not.toHaveBeenCalled();
  });

  it('resolveBackgroundTransparency prefers the live preview override', () => {
    setBackgroundTransparencyPreview(25);
    expect(resolveBackgroundTransparency(100)).toBe(25);
    setBackgroundTransparencyPreview(null);
    expect(resolveBackgroundTransparency(100)).toBe(100);
  });
});
