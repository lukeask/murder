/**
 * `clipboardImage` — read a pasted image off the Linux system clipboard, client-side.
 *
 * This is the F9 port of Textual's `app/tui/clipboard_image.py`. It MUST stay client-side: the TUI
 * owns the user's tty/desktop session, while the service is a headless daemon with no clipboard
 * access. So this shells the desktop clipboard tools directly from the Ink process — Wayland's
 * `wl-paste` when `WAYLAND_DISPLAY` is set, X11's `xclip` otherwise — mirroring the Textual source
 * exactly (force `image/png`, no shell, swallow any failure → `null`).
 *
 * The transport for the *bytes* is unchanged and lives elsewhere (the `image.upload` JSON-RPC, see
 * {@link ../store/imageDraft/imageDraftStore.js}); this module only does the local read.
 */

import { execFile } from 'node:child_process';

/** A clipboard image read: the raw bytes plus the extension to store it under. */
export interface ClipboardImage {
  /** The raw image bytes (PNG). */
  readonly bytes: Buffer;
  /** The file extension (always `'png'` — the source only ever reads `image/png`). */
  readonly ext: string;
}

/** Run a command with no shell, capturing stdout as a Buffer. Resolves `null` on any non-zero exit
 * or spawn error (the tool is missing, no image on the clipboard, etc.) — never throws, matching the
 * Textual source's blanket `except`. `maxBuffer` is bumped to hold multi-MB images. */
function execCapture(file: string, args: readonly string[]): Promise<Buffer | null> {
  return new Promise((resolve) => {
    execFile(
      file,
      args as string[],
      { encoding: 'buffer', maxBuffer: 64 * 1024 * 1024 },
      (error, stdout) => {
        if (error || !(stdout instanceof Buffer) || stdout.length === 0) {
          resolve(null);
          return;
        }
        resolve(stdout);
      },
    );
  });
}

/**
 * Read a PNG image from the system clipboard. Returns `{ bytes, ext: 'png' }` when the clipboard
 * holds an image, or `null` on any failure (no image, missing tool, empty output) — never throws.
 *
 * Wayland vs X11 is decided by `WAYLAND_DISPLAY`, exactly as the Textual source did. Each backend is
 * asked specifically for `image/png`, so a clipboard holding only text yields empty output → `null`.
 */
export async function readClipboardImage(): Promise<ClipboardImage | null> {
  const onWayland = Boolean(process.env['WAYLAND_DISPLAY']);
  const bytes = onWayland
    ? await execCapture('wl-paste', ['--type', 'image/png'])
    : await execCapture('xclip', ['-selection', 'clipboard', '-t', 'image/png', '-o']);
  if (bytes === null) {
    return null;
  }
  return { bytes, ext: 'png' };
}
