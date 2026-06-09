/**
 * `useTerminalSize` — the live terminal dimensions (`rows`/`columns`), tracked across resizes.
 *
 * Ink's root `<Box>` constrains *width* to the terminal but lets *height* be content-driven: a frame
 * taller than the terminal overflows, and because Ink can only erase up to the screen height it can
 * no longer redraw in place — every re-render then stacks a fresh full copy into scrollback. The cure
 * is to bound the app's root box to the terminal height so the whole frame always fits one screen
 * (panels clip/scroll within their boxes instead of growing the frame). That bound needs the live row
 * count, which is what this hook supplies — re-rendering the shell on a terminal resize.
 *
 * `useStdout()` gives the output stream; `stdout.rows`/`columns` are the current size and `'resize'`
 * fires on change. We seed from the current size and update on resize. Falls back to a sane 24×80 when
 * the stream reports nothing (a non-TTY/piped stdout — the same case that disables raw-mode input).
 */

import { useStdout } from 'ink';
import { useEffect, useState } from 'react';

export interface TerminalSize {
  readonly rows: number;
  readonly columns: number;
}

/** Read the current `{rows, columns}` off the stream, with a 24×80 fallback for a sizeless stdout. */
function readSize(stdout: NodeJS.WriteStream): TerminalSize {
  return { rows: stdout.rows ?? 24, columns: stdout.columns ?? 80 };
}

export function useTerminalSize(): TerminalSize {
  const { stdout } = useStdout();
  const [size, setSize] = useState<TerminalSize>(() => readSize(stdout));
  useEffect(() => {
    const onResize = (): void => setSize(readSize(stdout));
    // Re-sync once on mount in case the size changed between the initial state and the effect.
    onResize();
    stdout.on('resize', onResize);
    return () => {
      stdout.off('resize', onResize);
    };
  }, [stdout]);
  return size;
}
