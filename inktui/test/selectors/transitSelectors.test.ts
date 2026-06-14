/**
 * Git-Tree selector tests — the pure geometry: floored age, the ORDINAL column packing (one column
 * per commit, same-age cross-branch commits collapsing), word-wrap, run-length segmenting, the
 * swimlane DAG grid (tracks + stations + fork connectors), the shared ruler, duration parsing, and
 * the duration-jump resolving onto a pre-fork/main shared commit for a large duration.
 */

import { describe, expect, it } from 'vitest';
import type { DagColumn, TransitCursor } from '../../src/selectors/transitSelectors.js';
import {
  assignLaneHints,
  branchTag,
  buildColumnRuler,
  CELL_BLANK,
  CELL_SELECTED,
  CONNECTOR_CORNER,
  floorAge,
  HEAD_CAP,
  layoutDag,
  packColumns,
  parseDuration,
  resolveDurationJump,
  rowToSegments,
  SELECTED_GLYPH,
  STATION_FORK,
  tagColumnWidth,
  wrapText,
} from '../../src/selectors/transitSelectors.js';
import type { TransitCommit, TransitLane } from '../../src/store/transit/transitSlice.js';

const NOW = Date.parse('2026-06-12T12:00:00Z'); // ms
const SEC = Math.floor(NOW / 1000);

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
    tsEpoch: SEC - ageSec,
    parents: [],
    ...overrides,
  };
}

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

describe('floorAge', () => {
  it('floors minutes / hours / days / weeks, rounding down', () => {
    expect(floorAge(SEC - 30 * 60, NOW)).toBe('30m');
    expect(floorAge(SEC - (60 * 60 + 5 * 60), NOW)).toBe('1h'); // 1h05m → 1h
    expect(floorAge(SEC - 24 * 3600, NOW)).toBe('1d'); // exactly 24h → 1d
    expect(floorAge(SEC - 3 * 24 * 3600, NOW)).toBe('3d');
    expect(floorAge(SEC - 14 * 24 * 3600, NOW)).toBe('2w');
    expect(floorAge(SEC + 1000, NOW)).toBe('0m'); // future → 0m
  });
});

describe('packColumns — ordinal axis with same-age cross-branch collapse', () => {
  it("reproduces the worked two-branch example (the user's spec)", () => {
    // Two branches, each with only its own commits (forkSha null → own = all). Ages chosen so the
    // greedy same-label collapse pairs (b,B) and (D,d) but leaves c on its own.
    const b1 = laneOf(
      [
        commitAt('a', 3 * 60), // 3m
        commitAt('b', 60 * 60), // 1h
        commitAt('c', 60 * 60 + 32 * 60), // 1h32m → 1h
        commitAt('d', 24 * 3600 + 21 * 3600), // 1d21h → 1d
      ],
      { branch: 'b1' },
    );
    const b2 = laneOf(
      [
        commitAt('A', 10 * 60), // 10m
        commitAt('B', 60 * 60 + 31 * 60), // 1h31m → 1h
        commitAt('C', 2 * 3600 + 34 * 60), // 2h34m → 2h
        commitAt('D', 24 * 3600 + 4 * 3600), // 1d4h → 1d
      ],
      { branch: 'b2', isMain: false },
    );
    const cols = packColumns([b1, b2], NOW); // lane index 0 = b1, 1 = b2
    // Six columns, newest→oldest.
    expect(cols.map((c) => c.label)).toEqual(['3m', '10m', '1h', '1h', '2h', '1d']);
    // col0: only b1's `a`.
    expect([...(cols[0]?.byLane ?? [])]).toEqual([[0, 'a']]);
    // col2: b's 1h column PAIRS b1:b with b2:B (different branches, same floored label).
    expect(cols[2]?.byLane.get(0)).toBe('b');
    expect(cols[2]?.byLane.get(1)).toBe('B');
    // col3: c is alone (b1 row already used in col2, so it can't join — gets its own 1h column).
    expect([...(cols[3]?.byLane ?? [])]).toEqual([[0, 'c']]);
    // col5: the 1d column pairs b2:D with b1:d.
    expect(cols[5]?.byLane.get(1)).toBe('D');
    expect(cols[5]?.byLane.get(0)).toBe('d');
  });

  it('keeps same-lane same-age commits in separate columns', () => {
    // Two main commits both ~1h → they can't share a column (same lane row), so two 1h columns.
    const main = laneOf([commitAt('x', 60 * 60), commitAt('y', 60 * 60 + 40 * 60)]);
    const cols = packColumns([main], NOW);
    expect(cols.map((c) => c.label)).toEqual(['1h', '1h']);
    expect(cols[0]?.byLane.get(0)).toBe('x');
    expect(cols[1]?.byLane.get(0)).toBe('y');
  });
});

describe('wrapText', () => {
  it('greedy-wraps on spaces to the column budget', () => {
    expect(wrapText('the quick brown fox', 10)).toEqual(['the quick', 'brown fox']);
  });

  it('hard-splits an overlong token', () => {
    expect(wrapText('supercalifragilistic', 8)).toEqual(['supercal', 'ifragili', 'stic']);
  });

  it('preserves explicit newlines and returns [] for empty/zero-width', () => {
    expect(wrapText('a\nb', 10)).toEqual(['a', 'b']);
    expect(wrapText('', 10)).toEqual([]);
    expect(wrapText('hi', 0)).toEqual([]);
  });
});

describe('rowToSegments — run-length colors', () => {
  it('collapses adjacent same-color cells and splits on color changes', () => {
    const chars = ['a', 'b', 'c', 'd'];
    const colors = [0, 0, CELL_SELECTED, 0];
    expect(rowToSegments(chars, colors)).toEqual([
      { text: 'ab', color: 0 },
      { text: 'c', color: CELL_SELECTED },
      { text: 'd', color: 0 },
    ]);
  });

  it('keeps blank runs as their own segment', () => {
    expect(rowToSegments([' ', ' ', 'x'], [CELL_BLANK, CELL_BLANK, 1])).toEqual([
      { text: '  ', color: CELL_BLANK },
      { text: 'x', color: 1 },
    ]);
  });
});

describe('branchTag / tagColumnWidth', () => {
  it('wraps a branch name in the ▐ … ⌂ ▌ tag', () => {
    expect(branchTag('main')).toBe('▐ main ⌂ ▌');
  });

  it('clips an overlong branch name with an ellipsis', () => {
    const tag = branchTag('a-very-long-branch-name-indeed');
    expect(tag).toContain('…');
    expect(tag.startsWith('▐ a-very-long-bra…')).toBe(true);
  });

  it('reports the widest lane tag width', () => {
    const lanes = [laneOf([], { branch: 'main' }), laneOf([], { branch: 'webandmobile' })];
    expect(tagColumnWidth(lanes)).toBe(branchTag('webandmobile').length);
  });
});

describe('layoutDag — swimlane grid', () => {
  // Select a MIDDLE commit so both the HEAD cap (on the newest) and the selected glyph appear.
  const cursor: TransitCursor = { laneIndex: 0, sha: 'b' };

  it('draws main as a colored track newest-on-the-right with a HEAD cap and selected glyph', () => {
    const commits = [commitAt('a', 60), commitAt('b', 7200), commitAt('c', 172800)];
    const { laneRows, railwayWidth } = layoutDag([laneOf(commits)], cursor, 40, NOW);
    const row = laneRows[0];
    expect(row).toBeDefined();
    const text = (row?.segments ?? []).map((s) => s.text).join('');
    expect(text.length).toBe(railwayWidth);
    expect(text).toContain(HEAD_CAP); // newest tip cap
    expect(text).toContain(SELECTED_GLYPH); // cursor sits on 'a'
    // The selected glyph is painted with the CELL_SELECTED color.
    const selectedSeg = (row?.segments ?? []).find((s) => s.text.includes(SELECTED_GLYPH));
    expect(selectedSeg?.color).toBe(CELL_SELECTED);
  });

  it('scrolls the column window to keep the selected commit visible when it sits in an old column', () => {
    // More commits than fit: at innerWidth 40, railwayWidth = 40 - tag(10) - gap(1) = 29, so
    // fit = floor((29-1)/4)+1 = 8 columns. With 12 distinct-age commits the 8 newest would clip the
    // 4 oldest off the left. Navigate the cursor onto the OLDEST commit and assert its ◆ still draws.
    const commits = Array.from({ length: 12 }, (_, i) =>
      // Each commit a distinct hour older so every commit gets its own column (no same-age collapse).
      commitAt(`c${i}`, (i + 1) * 3600),
    );
    const oldest = commits[commits.length - 1];
    expect(oldest).toBeDefined();
    const oldCursor: TransitCursor = { laneIndex: 0, sha: oldest?.sha ?? '' };
    const { laneRows } = layoutDag([laneOf(commits)], oldCursor, 40, NOW);
    const row = laneRows[0];
    const text = (row?.segments ?? []).map((s) => s.text).join('');
    // The selected glyph must be drawn (the window scrolled older to include the cursor's column).
    expect(text).toContain(SELECTED_GLYPH);
    const selectedSeg = (row?.segments ?? []).find((s) => s.text.includes(SELECTED_GLYPH));
    expect(selectedSeg?.color).toBe(CELL_SELECTED);
    // It scrolled to the OLD end: the oldest commit is the cursor, so no HEAD cap is in view.
    expect(text).not.toContain(HEAD_CAP);
  });

  it('draws a branch as its own colored row with a fork tee on main and a corner where it forks', () => {
    // main: 4 commits; branch forks off main's 3rd commit (`m2`) and adds 2 of its own.
    const m0 = commitAt('m0', 60);
    const m1 = commitAt('m1', 3600);
    const m2 = commitAt('m2', 7200); // fork base, lives on main
    const m3 = commitAt('m3', 86400);
    const b0 = commitAt('b0', 120);
    const b1 = commitAt('b1', 1800);
    const main = laneOf([m0, m1, m2, m3]);
    const branch: TransitLane = {
      branch: 'feature',
      isMain: false,
      worktreePath: '/wt/f',
      headSha: 'b0',
      forkSha: 'm2',
      commits: [b0, b1, m2, m3], // own commits then shared pre-fork ancestry
    };
    const { laneRows } = layoutDag([main, branch], { laneIndex: 0, sha: 'm0' }, 50, NOW);
    const mainText = (laneRows[0]?.segments ?? []).map((s) => s.text).join('');
    const branchText = (laneRows[1]?.segments ?? []).map((s) => s.text).join('');
    // main's row carries the fork tee; the branch's row carries its turn-up corner.
    expect(mainText).toContain(STATION_FORK);
    expect(branchText).toContain(CONNECTOR_CORNER);
    // The branch's own stations are reported (b0/b1), not the shared ancestry.
    expect(laneRows[1]?.stationShas).toEqual(['b0', 'b1']);
    // The fork tee on main is painted in the BRANCH's color (lane index 1), not main's.
    const teeSeg = (laneRows[0]?.segments ?? []).find((s) => s.text.includes(STATION_FORK));
    expect(teeSeg?.color).toBe(1);
  });
});

describe('buildColumnRuler', () => {
  // The six worked-example columns (newest→oldest): 3m, 10m, 1h(b,B), 1h(c), 2h, 1d.
  const cols: DagColumn[] = [
    { label: '3m', byLane: new Map([[0, 'a']]) },
    { label: '10m', byLane: new Map([[1, 'A']]) },
    {
      label: '1h',
      byLane: new Map([
        [0, 'b'],
        [1, 'B'],
      ]),
    },
    { label: '1h', byLane: new Map([[0, 'c']]) },
    { label: '2h', byLane: new Map([[1, 'C']]) },
    {
      label: '1d',
      byLane: new Map([
        [1, 'D'],
        [0, 'd'],
      ]),
    },
  ];

  it('places deduped labels oldest→newest left→right (the second 1h column is blank)', () => {
    const width = 24;
    const xOf = (k: number): number => width - 1 - k * 4; // stride 4
    const ruler = buildColumnRuler(cols, xOf, width);
    expect(ruler.length).toBe(width);
    // Left→right order: 1d, 2h, 1h, 10m, 3m (the c column's duplicate 1h is deduped to blank).
    const order = ['1d', '2h', '1h', '10m', '3m'].map((t) => ruler.indexOf(t));
    expect(order).toEqual([...order].sort((a, b) => a - b)); // strictly increasing
    expect(order.every((i) => i >= 0)).toBe(true);
    // Only ONE `1h` (the c column deduped).
    expect(ruler.split('1h').length - 1).toBe(1);
    // 3m hugs the right edge.
    expect(ruler.trimEnd().endsWith('3m')).toBe(true);
  });

  it('returns an empty string for zero width', () => {
    expect(buildColumnRuler(cols, () => 0, 0)).toBe('');
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
    const resolved = resolveDurationJump(lane, 20 * 24 * 60 * 60_000, NOW);
    expect(resolved?.sha).toBe('shared');
    expect(resolved?.index).toBe(2);
    const deep = resolveDurationJump(lane, 30 * 24 * 60 * 60_000, NOW);
    expect(deep?.sha).toBe('older-main');
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
    const lanes: TransitLane[] = ['main', 'pane-polish', 'scrollbar'].map((branch) =>
      laneOf([], { branch, isMain: branch === 'main' }),
    );
    const hints = assignLaneHints(lanes);
    expect(hints.get('main')).toBe('m');
    expect(hints.get('pane-polish')).toBe('p');
    expect(hints.get('scrollbar')).toBe('s');
    expect(new Set(hints.values()).size).toBe(3);
  });

  it('falls back to a distinct char when first letters collide', () => {
    const lanes: TransitLane[] = ['main', 'master'].map((branch) =>
      laneOf([], { branch, isMain: branch === 'main' }),
    );
    const hints = assignLaneHints(lanes);
    expect(hints.get('main')).toBe('m');
    expect(hints.get('master')).toBe('a'); // 'm' taken → next free char 'a'
    expect(hints.get('main')).not.toBe(hints.get('master'));
  });
});
