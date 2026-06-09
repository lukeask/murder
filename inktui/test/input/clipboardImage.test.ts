/**
 * `clipboardImage` tests — the F9 client-side clipboard read (port of Textual's `clipboard_image.py`).
 *
 * `node:child_process` is mocked so no real `wl-paste`/`xclip` is shelled: we assert the *right tool
 * with the right args* is chosen (Wayland vs X11 by `WAYLAND_DISPLAY`), and that any failure or empty
 * output collapses to `null` rather than throwing (mirroring the source's blanket swallow).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const execFileMock = vi.hoisted(() => vi.fn());

vi.mock('node:child_process', () => ({ execFile: execFileMock }));

// Imported after the mock is registered.
import { readClipboardImage } from '../../src/input/clipboardImage.js';

/** Make `execFile` invoke its callback with `(error, stdout)`. */
function stubExecFile(error: Error | null, stdout: Buffer | null): void {
  execFileMock.mockImplementation((_file, _args, _opts, cb) => {
    cb(error, stdout);
  });
}

describe('readClipboardImage', () => {
  const savedWayland = process.env['WAYLAND_DISPLAY'];

  beforeEach(() => {
    execFileMock.mockReset();
  });

  afterEach(() => {
    if (savedWayland === undefined) {
      delete process.env['WAYLAND_DISPLAY'];
    } else {
      process.env['WAYLAND_DISPLAY'] = savedWayland;
    }
  });

  it('shells wl-paste --type image/png under Wayland and returns {bytes, ext:png}', async () => {
    process.env['WAYLAND_DISPLAY'] = 'wayland-0';
    const png = Buffer.from('\x89PNG-bytes');
    stubExecFile(null, png);

    const result = await readClipboardImage();

    expect(result).not.toBeNull();
    expect(result?.ext).toBe('png');
    expect(result?.bytes.equals(png)).toBe(true);
    const [file, args] = execFileMock.mock.calls[0] as [string, string[]];
    expect(file).toBe('wl-paste');
    expect(args).toEqual(['--type', 'image/png']);
  });

  it('shells xclip with the clipboard image/png target under X11', async () => {
    delete process.env['WAYLAND_DISPLAY'];
    stubExecFile(null, Buffer.from('img'));

    await readClipboardImage();

    const [file, args] = execFileMock.mock.calls[0] as [string, string[]];
    expect(file).toBe('xclip');
    expect(args).toEqual(['-selection', 'clipboard', '-t', 'image/png', '-o']);
  });

  it('returns null on a non-zero exit / spawn error (no image, missing tool)', async () => {
    delete process.env['WAYLAND_DISPLAY'];
    stubExecFile(new Error('exit 1'), null);
    expect(await readClipboardImage()).toBeNull();
  });

  it('returns null on empty output (clipboard holds no image)', async () => {
    delete process.env['WAYLAND_DISPLAY'];
    stubExecFile(null, Buffer.alloc(0));
    expect(await readClipboardImage()).toBeNull();
  });
});
