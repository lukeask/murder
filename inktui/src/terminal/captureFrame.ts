import type { CapturedFrame } from '../input/workspaceStore.js';
import { displayWidth } from '../render/frameText.js';
import { inkInstances } from './inkInstances.js';

/** The one Ink-internal field the capture path reads: the last frame string the renderer wrote
 * (rows joined by newlines, ANSI included). Same fragile-surface discipline as
 * {@link ./forceInkRepaint.js}: everything is validated before use, and any mismatch is a `null`
 * capture — never a crash, never garbage. */
interface InkInternals {
  lastOutput?: string;
}

/**
 * Capture the frame currently on screen as slide-animation source material (workspaces plan, step
 * 4b). Reads Ink's private `lastOutput` — the exact string the renderer last wrote — so this is a
 * zero-cost grab, not a re-render.
 *
 * Validate-or-no-op discipline: returns `null` (the pipeline then switches instantly, no slide)
 * whenever
 *  - Ink's private surface moved (no instance for this stdout, `lastOutput` missing/not a string),
 *  - the frame is empty (nothing rendered yet),
 *  - the terminal size is unknown/degenerate, or
 *  - the frame doesn't fit the declared size (more lines than rows, or a row wider than columns —
 *    e.g. a capture racing a resize), which would make the frame-text compositors throw.
 *
 * The returned frame is never truth — the real view repaints from the live tree at commit; this is
 * only ever pixels for the slide.
 */
export function captureCurrentFrame(stdout: NodeJS.WriteStream): CapturedFrame | null {
  try {
    const ink = inkInstances.get(stdout) as InkInternals | undefined;
    if (ink === undefined || typeof ink.lastOutput !== 'string' || ink.lastOutput.length === 0) {
      return null;
    }
    const { columns, rows } = stdout;
    if (
      typeof columns !== 'number' ||
      typeof rows !== 'number' ||
      !Number.isInteger(columns) ||
      !Number.isInteger(rows) ||
      columns <= 0 ||
      rows <= 0
    ) {
      return null;
    }
    // Ink's frame string has no trailing newline, but strip one defensively so a future Ink change
    // can't manufacture a phantom blank row.
    const text = ink.lastOutput.endsWith('\n') ? ink.lastOutput.slice(0, -1) : ink.lastOutput;
    const lines = text.split('\n');
    if (lines.length > rows) {
      return null;
    }
    for (const line of lines) {
      if (displayWidth(line) > columns) {
        return null;
      }
    }
    return { text, columns, rows };
  } catch {
    // Best-effort: a capture must never crash the app (mirrors forceInkFullRepaint).
    return null;
  }
}
