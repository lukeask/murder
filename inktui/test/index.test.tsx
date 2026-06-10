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

import { describe, expect, it } from 'vitest';
import { resolveSocketPath } from '../src/index.js';

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
