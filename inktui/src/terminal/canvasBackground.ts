/**
 * `canvasBackground` — apply / restore the app canvas fill for Background Transparency.
 *
 * At transparency 100 the canvas is omitted (terminal default shows through). Below 100, Ink paints
 * `canvasBg` on the root; on Kitty we also register that hex via OSC 21. On other terminals we
 * set dynamic color 11 while the canvas is painted, so terminal padding and otherwise-unpainted
 * cells cannot retain a transparent/default background.
 * `transparent_background_color1` at opacity `(100 - transparency) / 100` so wallpaper can bleed
 * through when the window's `background_opacity` is already < 1. Outside Kitty intermediate values
 * paint opaque (no true wallpaper alpha).
 *
 * Color-stack push/pop keeps the user's Kitty palette intact across murder sessions.
 */

import { useSyncExternalStore } from 'react';

const OSC = '\x1b]';
const ST = '\x1b\\';
const PUSH_COLORS = `${OSC}30001${ST}`;
const POP_COLORS = `${OSC}30101${ST}`;

const CLEANUP_SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP'] as const;

let cleanupInstalled = false;
/** Whether we have an unmatched OSC 30001 push outstanding. */
let stackPushed = false;
/** Whether we changed the non-Kitty dynamic default background (OSC 11). */
let defaultBackgroundSet = false;
/** Last applied `(transparency, canvasHex)` — skip redundant writes. */
let lastApplied: { transparency: number; canvasHex: string } | null = null;
/** Live-preview override while the settings modal browses transparency rows (`null` = use persisted). */
let previewTransparency: number | null = null;
const previewListeners = new Set<() => void>();

function inKitty(): boolean {
  return Boolean(process.env['KITTY_WINDOW_ID']);
}

function clampTransparency(value: number): number {
  if (!Number.isFinite(value)) {
    return 100;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function cellOpacity(transparency: number): number {
  return (100 - clampTransparency(transparency)) / 100;
}

function writeOsc(sequence: string): void {
  process.stdout.write(sequence);
}

function notifyPreviewListeners(): void {
  for (const listener of previewListeners) {
    listener();
  }
}

/** Live-preview a transparency value (settings modal cursor). Pass `null` to clear. */
export function setBackgroundTransparencyPreview(value: number | null): void {
  const next = value === null ? null : clampTransparency(value);
  if (previewTransparency === next) {
    return;
  }
  previewTransparency = next;
  notifyPreviewListeners();
}

/** Current live-preview override, or `null` when none. */
export function getBackgroundTransparencyPreview(): number | null {
  return previewTransparency;
}

/** Effective transparency: preview wins when set. */
export function resolveBackgroundTransparency(persisted: number): number {
  return previewTransparency ?? clampTransparency(persisted);
}

function subscribePreview(listener: () => void): () => void {
  previewListeners.add(listener);
  return () => {
    previewListeners.delete(listener);
  };
}

/** React: re-render when the settings-modal preview override changes. */
export function useBackgroundTransparencyPreview(): number | null {
  return useSyncExternalStore(subscribePreview, getBackgroundTransparencyPreview, () => null);
}

/**
 * Apply or clear terminal registrations for the canvas color.
 * Idempotent; safe to call on every settings/theme change.
 */
export function applyCanvasBackground(transparency: number, canvasHex: string): void {
  const t = clampTransparency(transparency);
  if (
    lastApplied !== null &&
    lastApplied.transparency === t &&
    lastApplied.canvasHex === canvasHex
  ) {
    return;
  }

  if (t >= 100) {
    restoreCanvasBackground();
    lastApplied = { transparency: 100, canvasHex };
    return;
  }

  if (!inKitty()) {
    // OSC 11 is deliberately opaque: non-Kitty terminals do not have a portable alpha equivalent.
    ensureCanvasBackgroundCleanup();
    writeOsc(`${OSC}11;${canvasHex}${ST}`);
    defaultBackgroundSet = true;
    lastApplied = { transparency: t, canvasHex };
    return;
  }

  ensureCanvasBackgroundCleanup();

  if (!stackPushed) {
    writeOsc(PUSH_COLORS);
    stackPushed = true;
  }

  const opacity = cellOpacity(t);
  // Default background + one transparent slot so unpainted cells and the painted canvasHex match.
  writeOsc(
    `${OSC}21;background=${canvasHex};transparent_background_color1=${canvasHex}@${opacity}${ST}`,
  );
  lastApplied = { transparency: t, canvasHex };
}

/** Restore the terminal background registrations we changed. Idempotent. */
export function restoreCanvasBackground(): void {
  if (!inKitty()) {
    if (defaultBackgroundSet) {
      // OSC 111 restores the terminal's configured dynamic default background. A future enhancement
      // may query OSC 11 first and restore a runtime override exactly.
      writeOsc(`${OSC}111${ST}`);
      defaultBackgroundSet = false;
    }
    lastApplied = null;
    return;
  }
  if (stackPushed) {
    writeOsc(POP_COLORS);
    stackPushed = false;
  }
  lastApplied = null;
}

/** Register process-level cleanup so terminal colors are restored on exit. Idempotent. */
export function ensureCanvasBackgroundCleanup(): void {
  if (cleanupInstalled) {
    return;
  }
  cleanupInstalled = true;

  const restore = (): void => {
    restoreCanvasBackground();
  };

  process.on('exit', restore);

  for (const signal of CLEANUP_SIGNALS) {
    const handler = (): void => {
      restore();
      process.removeListener(signal, handler);
      process.kill(process.pid, signal);
    };
    process.on(signal, handler);
  }
}

/** Test seam — reset module state between cases. */
export function _resetCanvasBackgroundForTests(): void {
  stackPushed = false;
  defaultBackgroundSet = false;
  lastApplied = null;
  previewTransparency = null;
  cleanupInstalled = false;
}
