import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** The subset of Ink's internal renderer instance this repaint path drives. */
interface InkInternals {
  log?: {
    reset?: () => void;
  };
  throttledLog?: {
    cancel?: () => void;
  };
  throttledOnRender?: {
    cancel?: () => void;
  };
  lastOutput?: string;
  lastOutputToRender?: string;
  lastOutputHeight?: number;
  calculateLayout?: () => void;
  onRender?: () => void;
}

/** Ink keeps one renderer per stdout in an internal WeakMap (not exported from the package). */
const inkInstances = createRequire(
  join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'node_modules', 'ink', 'build', 'instances.js'),
)('./instances.js').default as WeakMap<NodeJS.WriteStream, InkInternals>;

/** ED2 (erase entire screen) + cursor home — a physical wipe of the alternate screen buffer. */
const ERASE_SCREEN_AND_HOME = '\u001B[2J\u001B[H';

/** Synchronized-output bracket (DEC mode 2026) — the terminal buffers everything between begin and
 * end and commits it as one atomic frame. Terminals without support ignore both sequences. */
const BEGIN_SYNC = '\u001B[?2026h';
const END_SYNC = '\u001B[?2026l';

/**
 * Force a genuine full repaint after alternate-screen disturbances (resize, tmux messages, etc.).
 *
 * The naive candidates are both broken under `incrementalRendering`:
 *
 *  - `instance.clear()` erases the screen then `log.sync(lastOutput)`s the incremental line cache
 *    without writing — the screen goes blank while the cache believes every line is on screen, so
 *    the next incremental frame skips unchanged rows (e.g. the static top bar).
 *  - `writeToStdout('')` (the patched-`console.*` path) *replays* `lastOutputToRender` — a frame
 *    laid out for the **old** terminal width. On a shrink those lines hard-wrap, one logical line
 *    occupies two physical rows, and every later incremental `cursorUp(previousLines.length - 1)`
 *    is off by the wrap count — the garbled-border / merged-top-bar bug.
 *
 * So instead this does the real thing, in order:
 *
 *  1. **Physically erase** the screen and home the cursor (ED2 + CUP — no scrollback exists on the
 *     alternate screen, so this is a clean slate, not lost history).
 *  2. **Reset the incremental cache** (`log.reset()`) and blank `lastOutput*` so the renderer's
 *     `previousOutput.length === 0` branch fires — a full-frame write with fresh cursor math, no
 *     diffing against lines that are no longer where the cache thinks they are.
 *  3. **Re-layout and re-render from the live component tree** (`calculateLayout()` + `onRender()`),
 *     so the frame written is the *current* one at the *current* size — never a replay of stale
 *     output. `onRender` recomputes output from the root node, so this stays correct even when a
 *     throttled trailing render is still pending.
 *
 * All the internals are checked before the erase is written: if Ink's private surface moved (an
 * upgrade), this is a no-op rather than a blank screen.
 */
export function forceInkFullRepaint(stdout: NodeJS.WriteStream): void {
  try {
    const ink = inkInstances.get(stdout);
    if (
      ink === undefined ||
      typeof ink.log?.reset !== 'function' ||
      typeof ink.calculateLayout !== 'function' ||
      typeof ink.onRender !== 'function'
    ) {
      // Ink internals moved or stdout has no live instance — no-op (never blank without repaint).
      return;
    }
    // Ink's own resize pass runs before ours and may have a trailing throttled write queued with
    // stale output. Cancel it before the physical wipe so old bytes cannot land after END_SYNC.
    ink.throttledLog?.cancel?.();
    ink.throttledOnRender?.cancel?.();

    // Bracket the erase + fresh frame in synchronized output so the terminal never displays the
    // blank intermediate state — without this, the erase alone can show as a one-frame flicker
    // (the very bug incremental rendering was enabled to kill). Ink's own render writes a nested
    // begin/end pair; the inner end closes the bracket after the frame bytes, which is fine.
    stdout.write(BEGIN_SYNC + ERASE_SCREEN_AND_HOME);
    try {
      ink.log.reset();
      ink.lastOutput = '';
      ink.lastOutputToRender = '';
      ink.lastOutputHeight = 0;
      ink.calculateLayout();
      ink.onRender();
    } finally {
      stdout.write(END_SYNC);
    }
  } catch {
    // Best-effort: a repaint must never crash the app.
  }
}
