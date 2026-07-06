import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Ink keeps one renderer per stdout in an internal WeakMap (not exported from the package). */
const inkInstances = createRequire(
  join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'node_modules', 'ink', 'build', 'instances.js'),
)('./instances.js').default as WeakMap<
  NodeJS.WriteStream,
  { writeToStdout?: (data: string) => void; clear?: () => void }
>;

/**
 * Force a genuine full repaint after alternate-screen disturbances (resize, tmux messages, etc.).
 *
 * `instance.clear()` is unsafe with `incrementalRendering`: Ink 7.0.5's `clear()` erases the
 * screen then calls `log.sync(lastOutput)`, re-seeding the incremental line cache without writing.
 * The screen goes blank while the cache believes lines are already shown, so the next incremental
 * frame skips unchanged rows (e.g. the static top bar) until their text changes.
 *
 * Ink's `writeToStdout` path (used for patched `console.*`) clears the log buffer and calls
 * `restoreLastOutput()`, which actually repaints — same mechanism, no junk bytes when `data` is ''.
 *
 * When Ink internals are unreachable, this is a no-op — safer than `clear()`, which blanks without
 * repainting.
 */
export function forceInkFullRepaint(stdout: NodeJS.WriteStream): void {
  try {
    const ink = inkInstances.get(stdout);
    if (typeof ink?.writeToStdout === 'function') {
      ink.writeToStdout('');
    }
  } catch {
    // Ink internals moved or stdout has no live instance — no-op.
  }
}
