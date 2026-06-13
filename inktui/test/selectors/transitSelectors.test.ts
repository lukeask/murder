/**
 * Transit selector tests — the pure geometry: floored age, sparse marker placement, the 2-station
 * left-margin windowing, duration parsing, and the duration-jump resolving onto a pre-fork/main
 * shared commit for a large duration.
 */

import { describe, expect, it } from 'vitest';
import {
  assignLaneHints,
  floorAge,
  layoutLane,
  parseDuration,
  placeAgeMarkers,
  resolveDurationJump,
  windowIndices,
} from '../../src/selectors/transitSelectors.js';
import type { TransitCommit, TransitLane } from '../../src/store/transit/transitSlice.js';

const NOW = Date.parse('2026-06-12T12:00:00Z'); // ms

/** A commit `ageSec` seconds before NOW. */
function commitAt(
  sha: string,
  ageSec: number,
  overrides: Partial<TransitCommit> = {},
): TransitCommit {
  return {
    sha,
    short: sha.slice(0, 7),
    subject: `subject ${sha}`,
    body: '',
    tsEpoch: Math.floor(NOW / 1000) - ageSec,
    parents: [],
    ...overrides,
  };
}

describe('floorAge', () => {
  it('floors minutes / hours / days / weeks, rounding down', () => {
    expect(floorAge(Math.floor(NOW / 1000) - 30 * 60, NOW)).toBe('30m');
    expect(floorAge(Math.floor(NOW / 1000) - (60 * 60 + 5 * 60), NOW)).toBe('1h'); // 1h05m → 1h
    expect(floorAge(Math.floor(NOW / 1000) - 24 * 3600, NOW)).toBe('1d'); // exactly 24h → 1d
    expect(floorAge(Math.floor(NOW / 1000) - 3 * 24 * 3600, NOW)).toBe('3d');
    expect(floorAge(Math.floor(NOW / 1000) - 14 * 24 * 3600, NOW)).toBe('2w');
    expect(floorAge(Math.floor(NOW / 1000) + 1000, NOW)).toBe('0m'); // future → 0m
  });
});

describe('placeAgeMarkers', () => {
  it('emits a label only when the floored value changes (newest→oldest)', () => {
    const sec = Math.floor(NOW / 1000);
    const stations = [
      { tsEpoch: sec - (60 * 60 + 5 * 60) }, // 1h05m → "1h"
      { tsEpoch: sec - (60 * 60 + 50 * 60) }, // 1h50m → still "1h" → null
      { tsEpoch: sec - 2 * 60 * 60 }, // 2h → "2h"
    ];
    expect(placeAgeMarkers(stations, NOW)).toEqual(['1h', null, '2h']);
  });

  it('aligns 1:1 with the stations array', () => {
    const sec = Math.floor(NOW / 1000);
    const stations = [{ tsEpoch: sec - 24 * 3600 }, { tsEpoch: sec - 25 * 3600 }];
    const markers = placeAgeMarkers(stations, NOW);
    expect(markers).toHaveLength(2);
    expect(markers[0]).toBe('1d'); // 24h → 1d
    expect(markers[1]).toBeNull(); // 25h → still 1d
  });
});

describe('windowIndices — 2-station left margin', () => {
  it('shows newest-fitting for a non-selected lane', () => {
    // 10 commits, 4 fit, no selection → newest indices 0..3.
    expect(windowIndices(10, 4, null)).toEqual([0, 1, 2, 3]);
  });

  it('keeps a ~2-station left margin when the selection scrolls older', () => {
    // 10 commits, 4 fit. Selecting index 6 (older than the newest-fitting window) scrolls back so
    // the selection sits SELECTED_LEFT_MARGIN(2) from the newest visible edge: start = 6 - 2 = 4 →
    // window [4,5,6,7], so 2 newer commits (indices 4,5) stay visible to the selection's right.
    expect(windowIndices(10, 4, 6)).toEqual([4, 5, 6, 7]);
  });

  it('clamps the window at the oldest commit (newest truncate on the right once scrolled back)', () => {
    // Selecting the oldest (index 9): desiredStart 7 but maxStart = 10 - 4 = 6 → window [6,7,8,9].
    expect(windowIndices(10, 4, 9)).toEqual([6, 7, 8, 9]);
  });

  it('does not scroll when the selection is within the newest-fitting window', () => {
    // Selecting index 2 (< fit=4) stays at the newest window 0..3.
    expect(windowIndices(10, 4, 2)).toEqual([0, 1, 2, 3]);
  });
});

describe('layoutLane', () => {
  function laneOf(commits: TransitCommit[], overrides: Partial<TransitLane> = {}): TransitLane {
    return {
      branch: 'main',
      isMain: true,
      worktreePath: null,
      headSha: commits[0]?.sha ?? '',
      forkSha: null,
      commits,
      ...overrides,
    };
  }

  it('builds a railway + aligned age line, newest on the right with a HEAD cap', () => {
    const commits = [commitAt('a', 60), commitAt('b', 7200), commitAt('c', 172800)];
    const layout = layoutLane(laneOf(commits), 40, 'a', NOW);
    // newest (a) is selected → highlighted glyph; railway ends with the HEAD cap ▶.
    expect(layout.railwayLine.endsWith('▶')).toBe(true);
    expect(layout.railwayLine).toContain('◉'); // selected station
    // stationShas are oldest→newest left→right.
    expect(layout.stationShas[layout.stationShas.length - 1]).toBe('a');
  });

  it('windows the selected lane keeping the selection visible', () => {
    const commits = Array.from({ length: 10 }, (_, i) => commitAt(`c${i}`, i * 3600));
    // Tight width fitting ~3 stations; select an old commit → it must appear in stationShas.
    const layout = layoutLane(laneOf(commits), 12, 'c6', NOW);
    expect(layout.stationShas).toContain('c6');
  });
});

describe('parseDuration', () => {
  it('parses m/h/d/w tokens into ms', () => {
    const MIN = 60_000;
    expect(parseDuration('20m')).toBe(20 * MIN);
    expect(parseDuration('2h')).toBe(2 * 60 * MIN);
    expect(parseDuration('5d')).toBe(5 * 24 * 60 * MIN);
    expect(parseDuration('20d')).toBe(20 * 24 * 60 * MIN);
    expect(parseDuration('1w')).toBe(7 * 24 * 60 * MIN);
  });

  it('rejects malformed tokens', () => {
    expect(parseDuration('5')).toBeNull();
    expect(parseDuration('d')).toBeNull();
    expect(parseDuration('5y')).toBeNull();
    expect(parseDuration('')).toBeNull();
  });
});

describe('resolveDurationJump', () => {
  it('lands on a pre-fork/main-shared commit for a large duration', () => {
    const sec = Math.floor(NOW / 1000);
    // A branch lane whose commits cross the fork into shared main ancestry:
    //  - recent branch-only commits (1d, 3d old)
    //  - the fork commit (`shared`, 18d old) and an older shared-main commit (`older-main`, 25d old).
    const commits: TransitCommit[] = [
      commitAt('tip', 1 * 24 * 3600),
      commitAt('mid', 3 * 24 * 3600),
      commitAt('shared', 18 * 24 * 3600), // the fork point, lives on main too
      commitAt('older-main', 25 * 24 * 3600),
    ];
    const lane: TransitLane = {
      branch: 'feature',
      isMain: false,
      worktreePath: '/wt/feature',
      headSha: 'tip',
      forkSha: 'shared',
      commits,
    };
    // g20d → ~20 days ago: closest commit is `shared` (18d) vs `older-main` (25d) → shared.
    const resolved = resolveDurationJump(lane, 20 * 24 * 60 * 60_000, NOW);
    expect(resolved?.sha).toBe('shared');
    expect(resolved?.index).toBe(2);
    // A much larger duration (g30d) resolves onto the deepest shared-main commit.
    const deep = resolveDurationJump(lane, 30 * 24 * 60 * 60_000, NOW);
    expect(deep?.sha).toBe('older-main');
    void sec;
  });

  it('returns null for an empty lane', () => {
    const lane: TransitLane = {
      branch: 'empty',
      isMain: false,
      worktreePath: null,
      headSha: '',
      forkSha: null,
      commits: [],
    };
    expect(resolveDurationJump(lane, 1000, NOW)).toBeNull();
  });
});

describe('assignLaneHints', () => {
  it('assigns a unique single letter per lane, preferring the branch first char', () => {
    const lanes: TransitLane[] = ['main', 'pane-polish', 'scrollbar'].map((branch) => ({
      branch,
      isMain: branch === 'main',
      worktreePath: null,
      headSha: 'x',
      forkSha: null,
      commits: [],
    }));
    const hints = assignLaneHints(lanes);
    expect(hints.get('main')).toBe('m');
    expect(hints.get('pane-polish')).toBe('p');
    expect(hints.get('scrollbar')).toBe('s');
    // All distinct.
    expect(new Set(hints.values()).size).toBe(3);
  });

  it('falls back to a distinct char when first letters collide', () => {
    const lanes: TransitLane[] = ['main', 'master'].map((branch) => ({
      branch,
      isMain: branch === 'main',
      worktreePath: null,
      headSha: 'x',
      forkSha: null,
      commits: [],
    }));
    const hints = assignLaneHints(lanes);
    expect(hints.get('main')).toBe('m');
    expect(hints.get('master')).toBe('a'); // 'm' taken → next free char 'a'
    expect(hints.get('main')).not.toBe(hints.get('master'));
  });
});
