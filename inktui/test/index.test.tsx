/**
 * Entrypoint runner test (F7). The live/smoke render paths use Ink's real `render`, which patches
 * `console` and cannot run under Vitest's environment (`ink-testing-library` is what the shell tests
 * use instead — see App.test.tsx); those paths are proven by an actual `node dist/index.js --smoke`
 * run at build time. What this file pins is the pure, load-bearing wiring F7 introduces:
 *
 *   - the socket path is taken from `MURDER_BUS_SOCKET` **verbatim** — the no-rehash invariant: the
 *     TS side never derives the per-project socket path, it only connects to what the launcher hands
 *     it (Open decision #2);
 *   - a missing/empty `MURDER_BUS_SOCKET` is a clear, hard failure rather than a silent bad connect.
 */

import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import React from 'react';
import { render, Text } from 'ink';
import { describe, expect, it, vi } from 'vitest';
import { forceInkFullRepaint, installResizeClear, resolveSocketPath } from '../src/index.js';

describe('resolveSocketPath', () => {
  it('returns MURDER_BUS_SOCKET verbatim (no rehashing the per-project path)', () => {
    const path = '/run/user/1000/murder/repo-abc123def456/bus.sock';
    expect(resolveSocketPath({ MURDER_BUS_SOCKET: path })).toBe(path);
  });

  it('throws a clear error naming the env var when it is unset', () => {
    expect(() => resolveSocketPath({})).toThrow(/MURDER_BUS_SOCKET is not set/);
  });

  it('throws when MURDER_BUS_SOCKET is empty or whitespace', () => {
    expect(() => resolveSocketPath({ MURDER_BUS_SOCKET: '   ' })).toThrow(/MURDER_BUS_SOCKET/);
  });
});

class FakeStdout extends EventEmitter {
  columns: number;
  rows: number;

  constructor(columns: number, rows: number) {
    super();
    this.columns = columns;
    this.rows = rows;
  }
}

describe('forceInkFullRepaint', () => {
  it('repaints via writeToStdout after clear() would blank the screen under incremental rendering', async () => {
    const stdout = new PassThrough() as PassThrough & {
      columns: number;
      rows: number;
      isTTY: boolean;
    };
    let written = '';
    stdout.isTTY = true;
    stdout.columns = 80;
    stdout.rows = 24;
    stdout.write = ((data: string) => {
      written += data;
      return true;
    }) as typeof stdout.write;
    stdout.on = () => stdout;
    stdout.off = () => stdout;

    const { clear, unmount } = render(<Text>top-bar</Text>, {
      stdout,
      stdin: process.stdin,
      patchConsole: false,
      alternateScreen: true,
      incrementalRendering: true,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(written.includes('top-bar')).toBe(true);

    written = '';
    clear();
    expect(written.includes('top-bar')).toBe(false);

    written = '';
    forceInkFullRepaint(stdout);
    expect(written.includes('top-bar')).toBe(true);

    unmount();
  });

  it('does nothing when stdout has no Ink instance (no unsafe clear fallback)', () => {
    const stdout = new FakeStdout(80, 24) as unknown as NodeJS.WriteStream;
    expect(() => forceInkFullRepaint(stdout)).not.toThrow();
  });
});

describe('installResizeClear', () => {
  it('clears on column, row, or combined terminal size changes', () => {
    const stdout = new FakeStdout(120, 40);
    const clear = vi.fn();
    const dispose = installResizeClear(stdout, clear);

    stdout.columns = 121;
    stdout.emit('resize');
    stdout.rows = 41;
    stdout.emit('resize');
    stdout.columns = 100;
    stdout.rows = 30;
    stdout.emit('resize');

    expect(clear).toHaveBeenCalledTimes(3);
    dispose();
  });

  it('ignores duplicate resize events and unregisters cleanly', () => {
    const stdout = new FakeStdout(120, 40);
    const clear = vi.fn();
    const dispose = installResizeClear(stdout, clear);

    stdout.emit('resize');
    expect(clear).not.toHaveBeenCalled();

    stdout.columns = 80;
    stdout.emit('resize');
    dispose();
    stdout.columns = 81;
    stdout.emit('resize');

    expect(clear).toHaveBeenCalledTimes(1);
  });
});
