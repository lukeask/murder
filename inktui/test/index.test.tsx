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
import { createRequire } from 'node:module';
import { PassThrough } from 'node:stream';
import { render, Text } from 'ink';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../src/bus/FakeBusClient.js';
import { App } from '../src/components/App.js';
import { createInputStores } from '../src/input/createInputStores.js';
import { forceInkFullRepaint, installResizeClear, resolveSocketPath } from '../src/index.js';
import { createAppStore } from '../src/store/store.js';

interface TestInkInternals {
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

const inkInstances = createRequire(import.meta.url)(
  '../node_modules/ink/build/instances.js',
).default as WeakMap<NodeJS.WriteStream, TestInkInternals>;

afterEach(() => {
  vi.useRealTimers();
});

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
      stdout: stdout as unknown as NodeJS.WriteStream,
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
    forceInkFullRepaint(stdout as unknown as NodeJS.WriteStream);
    expect(written.includes('top-bar')).toBe(true);

    unmount();
  });

  it('repaints the CURRENT frame at the CURRENT width after a resize (no stale-width replay)', async () => {
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

    const { unmount } = render(<Text wrap="truncate">{'x'.repeat(60)}</Text>, {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: process.stdin,
      patchConsole: false,
      alternateScreen: true,
      incrementalRendering: true,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(written.includes('x'.repeat(60))).toBe(true);

    // Shrink the terminal, then force the repaint (what installResizeClear wires up). The frame
    // written must be re-laid-out for the new width — truncated to 40 — not a replay of the
    // old 60-wide output (the stale replay is what garbled borders after resize).
    stdout.columns = 40;
    stdout.emit('resize');
    written = '';
    forceInkFullRepaint(stdout as unknown as NodeJS.WriteStream);
    await new Promise((resolve) => setTimeout(resolve, 20));
    // Physical screen erase precedes the fresh frame.
    expect(written.includes('\u001B[2J')).toBe(true);
    expect(written.includes('x'.repeat(60))).toBe(false);
    // `wrap="truncate"` at 40 columns yields 39 x's + an ellipsis.
    expect(written.includes(`${'x'.repeat(39)}…`)).toBe(true);

    unmount();
  });

  it('does nothing when stdout has no Ink instance (no unsafe clear fallback)', () => {
    const stdout = new FakeStdout(80, 24) as unknown as NodeJS.WriteStream;
    expect(() => forceInkFullRepaint(stdout)).not.toThrow();
  });

  it('cancels queued throttled writes before wiping so stale output cannot land after END_SYNC', async () => {
    vi.useFakeTimers();
    const stdout = new FakeStdout(80, 24) as unknown as NodeJS.WriteStream & {
      write: (data: string) => boolean;
    };
    let written = '';
    stdout.write = (data: string) => {
      written += data;
      return true;
    };
    const staleTimer = setTimeout(() => stdout.write('stale-after-end'), 0);
    const cancelStale = vi.fn(() => clearTimeout(staleTimer));
    const cancelRender = vi.fn();
    inkInstances.set(stdout, {
      log: { reset: vi.fn() },
      throttledLog: { cancel: cancelStale },
      throttledOnRender: { cancel: cancelRender },
      calculateLayout: vi.fn(),
      onRender: () => stdout.write('fresh-frame'),
    });

    forceInkFullRepaint(stdout);
    await vi.runAllTimersAsync();

    expect(cancelStale).toHaveBeenCalledTimes(1);
    expect(cancelRender).toHaveBeenCalledTimes(1);
    expect(written).toContain('\u001B[?2026h\u001B[2J\u001B[H');
    expect(written).toContain('fresh-frame');
    expect(written).toContain('\u001B[?2026l');
    expect(written).not.toContain('stale-after-end');
  });

  it('does one post-commit full repaint after React receives a new terminal size', async () => {
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

    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);
    const inputStores = createInputStores([]);
    const { unmount } = render(<App store={store} inputStores={inputStores} bus={fake} />, {
      stdout: stdout as unknown as NodeJS.WriteStream,
      stdin: process.stdin,
      patchConsole: false,
      alternateScreen: true,
      incrementalRendering: true,
    });
    await new Promise((resolve) => setTimeout(resolve, 20));

    written = '';
    stdout.rows = 25;
    stdout.emit('resize');
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(written.match(/\u001B\[2J/g)?.length ?? 0).toBe(1);

    unmount();
    dispose();
  });
});

describe('installResizeClear', () => {
  it('debounces column, row, and combined terminal size changes into one trailing clear', async () => {
    vi.useFakeTimers();
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

    expect(clear).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(74);
    expect(clear).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(clear).toHaveBeenCalledTimes(1);
    dispose();
  });

  it('ignores duplicate resize events and unregisters cleanly', async () => {
    vi.useFakeTimers();
    const stdout = new FakeStdout(120, 40);
    const clear = vi.fn();
    const dispose = installResizeClear(stdout, clear);

    stdout.emit('resize');
    expect(clear).not.toHaveBeenCalled();

    stdout.columns = 80;
    stdout.emit('resize');
    await vi.advanceTimersByTimeAsync(75);
    dispose();
    stdout.columns = 81;
    stdout.emit('resize');

    expect(clear).toHaveBeenCalledTimes(1);
  });
});
