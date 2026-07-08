/**
 * `captureCurrentFrame` tests — the Ink-internals frame grab (workspaces plan, step 4b).
 *
 * Cookbook first: a registered instance with a plausible `lastOutput` captures at the stream's
 * size. Then the validate-or-no-op edges: no instance, missing/empty `lastOutput`, unknown size,
 * and frames that don't fit the declared geometry (the resize race).
 *
 * The tests write into the SAME private WeakMap the production code reads (the `createRequire`
 * reach into `ink/build/instances.js`), so they pin the exact fragile surface: if Ink moves it,
 * both the code and these tests go no-op/red together.
 */

import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { captureCurrentFrame } from '../../src/terminal/captureFrame.js';

interface FakeInkInternals {
  lastOutput?: string | number;
}

const inkInstances = createRequire(
  join(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    '..',
    'node_modules',
    'ink',
    'build',
    'instances.js',
  ),
)('./instances.js').default as WeakMap<object, FakeInkInternals>;

/** A stdout stand-in: only `columns`/`rows` and identity (the WeakMap key) matter to the capture. */
function fakeStdout(columns?: number, rows?: number): NodeJS.WriteStream {
  return { columns, rows } as unknown as NodeJS.WriteStream;
}

function register(stdout: NodeJS.WriteStream, internals: FakeInkInternals): void {
  inkInstances.set(stdout, internals);
}

describe('captureCurrentFrame (cookbook)', () => {
  it('captures the last rendered frame at the stream size', () => {
    const stdout = fakeStdout(10, 3);
    register(stdout, { lastOutput: 'top\nmiddle' });
    expect(captureCurrentFrame(stdout)).toEqual({ text: 'top\nmiddle', columns: 10, rows: 3 });
  });

  it('keeps ANSI styling in the frame (width is measured with CSI stripped)', () => {
    const stdout = fakeStdout(5, 2);
    const styled = '\u001b[31mhello\u001b[39m';
    register(stdout, { lastOutput: styled });
    expect(captureCurrentFrame(stdout)).toEqual({ text: styled, columns: 5, rows: 2 });
  });

  it('strips a single trailing newline (no phantom blank row)', () => {
    const stdout = fakeStdout(10, 3);
    register(stdout, { lastOutput: 'a\nb\n' });
    expect(captureCurrentFrame(stdout)?.text).toBe('a\nb');
  });
});

describe('captureCurrentFrame (validate-or-no-op edges)', () => {
  it('returns null when no Ink instance is registered for the stream', () => {
    expect(captureCurrentFrame(fakeStdout(80, 24))).toBeNull();
  });

  it('returns null when lastOutput is missing, non-string, or empty', () => {
    const missing = fakeStdout(80, 24);
    register(missing, {});
    expect(captureCurrentFrame(missing)).toBeNull();

    const wrongType = fakeStdout(80, 24);
    register(wrongType, { lastOutput: 42 });
    expect(captureCurrentFrame(wrongType)).toBeNull();

    const empty = fakeStdout(80, 24);
    register(empty, { lastOutput: '' });
    expect(captureCurrentFrame(empty)).toBeNull();
  });

  it('returns null when the stream size is unknown or degenerate', () => {
    const sizeless = fakeStdout(undefined, undefined);
    register(sizeless, { lastOutput: 'frame' });
    expect(captureCurrentFrame(sizeless)).toBeNull();

    const zero = fakeStdout(0, 24);
    register(zero, { lastOutput: 'frame' });
    expect(captureCurrentFrame(zero)).toBeNull();
  });

  it('returns null when the frame does not fit the declared geometry (resize race)', () => {
    const tooTall = fakeStdout(10, 2);
    register(tooTall, { lastOutput: 'a\nb\nc' });
    expect(captureCurrentFrame(tooTall)).toBeNull();

    const tooWide = fakeStdout(4, 2);
    register(tooWide, { lastOutput: 'wider' });
    expect(captureCurrentFrame(tooWide)).toBeNull();
  });
});
