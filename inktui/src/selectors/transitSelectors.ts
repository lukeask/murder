/**
 * Git-Tree view-models — the selector (rule 2: ALL formatting/geometry lives here, never in the
 * store or the component). Every function in this file is PURE (no React, no Ink) and computes
 * deterministic line/column counts — NEVER relying on `measureElement` for wrapped text (memory
 * `project_inktui_measure_wrap`): the railway grid and the wrapped info lines are built to a known
 * inner width so Yoga never disagrees about their height.
 *
 * ## The model: swimlanes on an ORDINAL (rank) axis (a shared-ruler DAG)
 * The panel draws a small commit graph as SWIMLANES whose X axis is ORDINAL — one COLUMN per commit,
 * newest on the RIGHT — NOT proportional to time (so `1d→2h` jumps the same as `10m→3m`). This
 * maximizes how many commits show at once; the timestamps are fun extra info, the commits are the
 * focus. Key rules ({@link packColumns}):
 *  - all lanes' commits are merged newest→oldest into a shared column sequence; two adjacent-in-time
 *    commits from DIFFERENT branches that share the same floored age (e.g. `1h`/`1h31m`) collapse into
 *    ONE column (one station per lane row), so a single age RULER on top applies to all branches.
 *  - each LANE is a row: `main` first, then each `.murder/worktrees` branch. A lane draws only its
 *    OWN commits (those after its fork point); the shared pre-fork ancestry lives on main's row.
 *  - a branch FORKS from main with a vertical connector at its fork column (a `┳` tee on main, a
 *    `│` through any intervening rows, a `╰` corner where the branch's own track begins).
 *  - the SELECTED commit is a distinct glyph in the focus color ("where you are").
 *  - every cell carries a COLOR (a lane index, or the BLANK / SELECTED sentinels) so the component
 *    paints each branch in its own accent and the selected station in the focus color.
 *
 * Two layers like every selector: pure transforms (exported for unit tests) + a memoised
 * {@link useTransitView} hook bucketed on a minute `now` so ages tick without re-running per render.
 */

import { useMemo } from 'react';
import type { TransitCommit, TransitLane, TransitState } from '../store/transit/transitSlice.js';

// ── Glyphs ─────────────────────────────────────────────────────────────────────────────────────
/** The newest-end cap drawn at a lane's HEAD (the tip of the railway). */
export const HEAD_CAP = '▶';
/** A normal commit station. */
export const STATION = '○';
/** The selected commit station — a distinct "you are here" glyph (painted in the focus color). */
export const SELECTED_GLYPH = '◆';
/** A fork/merge junction: where a branch tees off main (drawn on main's row, in the branch color). */
export const STATION_FORK = '┳';
/** One cell of horizontal track between stations. */
export const TRACK = '━';
/** A DISCONTINUITY in the track: a dashed run drawn across a "jump" slot, where intervening commits
 * were elided to pull a branch's far-back fork vertex into view (so the `┳`/`╰` last-shared junction
 * always shows). Reads as "the line continues, but commits were skipped here". */
export const TRACK_GAP = '┄';
/** A vertical connector segment (a fork line passing through an intervening row). */
export const CONNECTOR_VERT = '│';
/** The corner where a branch's own track begins, turning up toward its fork tee (branches sit BELOW
 * main, so the corner turns UP-and-RIGHT). */
export const CONNECTOR_CORNER = '╰';

// ── Cell color sentinels ─────────────────────────────────────────────────────────────────────────
/** A blank (un-painted) cell — a space the component leaves uncolored. */
export const CELL_BLANK = -1;
/** The selected station's cell — the component paints it in the focus color. */
export const CELL_SELECTED = -2;
/** A painted cell's color: a lane index (≥0), or one of the {@link CELL_BLANK}/{@link CELL_SELECTED}
 * sentinels. The component maps a lane index → the lane's accent (main = `active`, else the
 * `laneColors` ring); the sentinels → blank / focus. */
export type CellColor = number;

/** The fixed gap (in cells) between the railway grid and the right-hand branch-tag column. */
export const TAG_GAP = 1;
/** A branch name is clipped to this many columns inside its `▐ name ⌂ ▌` tag (so one long branch
 * name can't blow out the tag column and starve the railway). */
export const BRANCH_NAME_CAP = 16;
/** Reserved info-body lines (wrapped commit message) under the sha line — the fixed height the panel
 * and the portrait height-budget both assume. */
export const INFO_BODY_LINES = 6;
/** Cells one station column consumes: the glyph + a fixed run of track to the next column. The axis
 * is ORDINAL (one column per commit, newest on the right) — NOT proportional to time — so this is a
 * constant stride, not a function of timestamps. 4 (glyph + 3 track) is chosen so a 3-char age label
 * (`10m`, `23h`, …) fits a column without clipping into its neighbor on the ruler. */
export const COL_STRIDE = 4;

/**
 * Floor a commit's age (unix epoch SECONDS) against `nowMs` to a coarse label, rounding DOWN:
 *  - `< 60m` → `"Nm"`, `< 24h` → `"Nh"`, `< 7d` → `"Nd"`, else `"Nw"`.
 * A future/negative delta floors to `"0m"`. Pure.
 */
export function floorAge(tsEpochSec: number, nowMs: number): string {
  const seconds = Math.max(0, Math.floor(nowMs / 1000 - tsEpochSec));
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  if (days < 7) {
    return `${days}d`;
  }
  const weeks = Math.floor(days / 7);
  return `${weeks}w`;
}

/** One ORDINAL column of the graph: a time-rank slot shared across lanes. `byLane` maps a lane index
 * to the sha that lane places in this column (a column holds at most one commit per lane); `label` is
 * the floored age all its commits share. */
export interface DagColumn {
  readonly label: string;
  readonly byLane: ReadonlyMap<number, string>;
}

/** A lane's OWN commits (newest-first): main's are all of them; a branch's are those before its fork
 * commit (the shared pre-fork ancestry belongs to main's row). Pure. */
function ownCommitsOf(lane: TransitLane): TransitCommit[] {
  const fi = forkIndex(lane);
  return fi >= 0 ? lane.commits.slice(0, fi) : [...lane.commits];
}

/**
 * Pack every lane's own commits into ORDINAL columns (the heart of the layout — time is NOT
 * proportional). Merge all commits newest→oldest, then GREEDILY (from the newest) start a column per
 * commit, EXCEPT a commit joins the current column when it shares that column's floored label AND its
 * lane row is still free in it — so two adjacent-in-time commits from different branches with the same
 * coarse age (e.g. `1h` and `1h31m`) collapse into one column, maximizing how many commits fit while
 * each commit keeps its own station. Returns columns NEWEST→OLDEST. Pure.
 */
export function packColumns(lanes: readonly TransitLane[], nowMs: number): DagColumn[] {
  interface Placed {
    readonly sha: string;
    readonly ts: number;
    readonly lane: number;
    readonly label: string;
  }
  const placed: Placed[] = [];
  lanes.forEach((lane, idx) => {
    for (const c of ownCommitsOf(lane)) {
      placed.push({ sha: c.sha, ts: c.tsEpoch, lane: idx, label: floorAge(c.tsEpoch, nowMs) });
    }
  });
  // Newest first; tie-break by lane index so the packing is deterministic.
  placed.sort((a, b) => b.ts - a.ts || a.lane - b.lane);

  const columns: { label: string; byLane: Map<number, string> }[] = [];
  let current: { label: string; byLane: Map<number, string> } | null = null;
  for (const p of placed) {
    if (current !== null && p.label === current.label && !current.byLane.has(p.lane)) {
      current.byLane.set(p.lane, p.sha);
    } else {
      current = { label: p.label, byLane: new Map([[p.lane, p.sha]]) };
      columns.push(current);
    }
  }
  return columns;
}

/** One slot in the laid-out, left→right-on-the-rail visible sequence. A `col` slot draws a real
 * {@link DagColumn} (at its ordinal `rank` in the full packed sequence); a `gap` slot is a
 * DISCONTINUITY — a "jump" rendered as dashed track where intervening commits were elided to pull a
 * far-back column (a branch's fork vertex) into view. Slots are ordered NEWEST→OLDEST (index 0 is the
 * newest, drawn at the right). */
export type VisibleSlot =
  | { readonly kind: 'col'; readonly rank: number; readonly column: DagColumn }
  | { readonly kind: 'gap' };

/** Count the rail slots a chosen set of column ranks needs: one per column, PLUS one `gap` slot for
 * each break between non-adjacent ranks (a single dashed jump collapses any number of skipped
 * columns). Pure helper for the budget in {@link planVisibleColumns}. */
function slotCost(ranks: readonly number[]): number {
  const sorted = [...ranks].sort((a, b) => a - b);
  let cost = sorted.length;
  for (let i = 1; i < sorted.length; i += 1) {
    if ((sorted[i] ?? 0) - (sorted[i - 1] ?? 0) > 1) {
      cost += 1; // one dashed jump bridges this break
    }
  }
  return cost;
}

/**
 * Decide which packed columns to show, and where the "jump" discontinuities go, within a `fit`-slot
 * budget. The newest column is always shown; the recent block grows contiguously from it; and the
 * REQUIRED far-back columns — each branch's fork vertex (its last commit shared with main) plus the
 * cursor's column — are PINNED into view even when they sit far in the past, each reached across a
 * single dashed `gap` slot. This is what lets the `┳`/`╰` last-split junction always show (with room
 * for its age label above), per the spec: "when there are ≥2 branches, jump back to the last shared
 * commit". Budget pressure drops the lowest-priority pins first (oldest forks), never the cursor.
 *
 * `forkRanks` are the ranks (in the full newest→oldest `allColumns`) that carry a branch fork on the
 * MAIN row; `cursorRank` is the cursor's column (or -1). Returns slots NEWEST→OLDEST. Pure.
 */
export function planVisibleColumns(
  allColumns: readonly DagColumn[],
  forkRanks: readonly number[],
  cursorRank: number,
  fit: number,
): VisibleSlot[] {
  const n = allColumns.length;
  if (n === 0 || fit <= 0) {
    return [];
  }
  const included = new Set<number>([0]); // the newest column always anchors the right edge
  // Pin the REQUIRED far-back columns by priority: the cursor first (selection must never vanish),
  // then forks newest→oldest. Each is added only if the running slot budget still fits.
  const pins: number[] = [];
  if (cursorRank >= 0) {
    pins.push(cursorRank);
  }
  for (const r of [...new Set(forkRanks)].sort((a, b) => a - b)) {
    pins.push(r);
  }
  for (const r of pins) {
    if (r < 0 || r >= n || included.has(r)) {
      continue;
    }
    if (slotCost([...included, r]) <= fit) {
      included.add(r);
    }
  }
  // Grow the recent block contiguously from the newest; each added rank either fills a slot or bridges
  // a jump (freeing it). Keep going while the budget holds — older commits surface as room allows.
  for (let r = 1; r < n; r += 1) {
    if (included.has(r)) {
      continue;
    }
    if (slotCost([...included, r]) <= fit) {
      included.add(r);
    }
  }
  // Emit slots newest→oldest, inserting one `gap` between any two non-adjacent included ranks.
  const ranks = [...included].sort((a, b) => a - b);
  const slots: VisibleSlot[] = [];
  for (let i = 0; i < ranks.length; i += 1) {
    const rank = ranks[i] ?? 0;
    if (i > 0 && rank - (ranks[i - 1] ?? 0) > 1) {
      slots.push({ kind: 'gap' });
    }
    const column = allColumns[rank];
    if (column !== undefined) {
      slots.push({ kind: 'col', rank, column });
    }
  }
  return slots;
}

/**
 * Greedy word-wrap `text` to `width` columns: split on existing newlines, then pack space-separated
 * words, hard-splitting any single token longer than `width`. Returns the wrapped lines (an empty
 * input yields `[]`). Deterministic — the info section renders exactly this many lines (no measure).
 */
export function wrapText(text: string, width: number): string[] {
  if (width <= 0 || text.length === 0) {
    return [];
  }
  const out: string[] = [];
  for (const rawLine of text.split('\n')) {
    if (rawLine.length === 0) {
      out.push('');
      continue;
    }
    let current = '';
    for (const word of rawLine.split(' ')) {
      let w = word;
      // Hard-split a token wider than the column budget.
      while (w.length > width) {
        if (current.length > 0) {
          out.push(current);
          current = '';
        }
        out.push(w.slice(0, width));
        w = w.slice(width);
      }
      if (current.length === 0) {
        current = w;
      } else if (current.length + 1 + w.length <= width) {
        current += ` ${w}`;
      } else {
        out.push(current);
        current = w;
      }
    }
    out.push(current);
  }
  return out;
}

/** Clip a branch name to {@link BRANCH_NAME_CAP} columns (keep the head; ellipsis the tail). Pure. */
export function clipBranch(name: string): string {
  if (name.length <= BRANCH_NAME_CAP) {
    return name;
  }
  return `${name.slice(0, BRANCH_NAME_CAP - 1)}…`;
}

/** The `▐ name ⌂ ▌` tag string for a branch (name clipped). Pure. */
export function branchTag(name: string): string {
  return `▐ ${clipBranch(name)} ⌂ ▌`;
}

/**
 * Build the shared age-ruler line over the visible SLOTS. `xOf(i)` is slot `i`'s x (i=0 is the newest,
 * at the right). Walking newest→oldest, a `col` slot's floored label is placed (left-aligned at its x)
 * only when it DIFFERS from the last emitted — so a run of same-age columns prints the label once,
 * against the newest of the run (e.g. two `1h` columns label only the newer one). A `gap` slot resets
 * that tracking so the column just past a jump ALWAYS re-prints its (older) age — giving the spec's
 * "room for the time label above that last shared vertex". Newer labels are placed first and win their
 * cells (blank-guard); a label overflowing the right edge is pulled back to fit. Returns a
 * `width`-wide string. Pure.
 */
export function buildColumnRuler(
  slots: readonly VisibleSlot[],
  xOf: (i: number) => number,
  width: number,
): string {
  if (width <= 0) {
    return '';
  }
  const cells: string[] = new Array(width).fill(' ');
  let last: string | null = null;
  for (let i = 0; i < slots.length; i += 1) {
    const slot = slots[i];
    if (slot === undefined || slot.kind === 'gap') {
      last = null; // a jump breaks the run — force the next column to re-label.
      continue;
    }
    const label = slot.column.label;
    if (label === last) {
      continue;
    }
    last = label;
    const x = xOf(i);
    const start = Math.max(0, Math.min(x, width - label.length));
    for (let j = 0; j < label.length && start + j < width; j += 1) {
      // Blank-guard: a newer column's label (placed earlier in this walk) keeps its cells.
      if (cells[start + j] === ' ') {
        cells[start + j] = label[j] ?? ' ';
      }
    }
  }
  return cells.join('');
}

/** One contiguous run of same-colored cells on a railway row. */
export interface RailSegment {
  readonly text: string;
  readonly color: CellColor;
}

/** Collapse a per-cell `(char, color)` row into contiguous same-color segments. Pure. */
export function rowToSegments(
  chars: readonly string[],
  colors: readonly CellColor[],
): RailSegment[] {
  const segments: RailSegment[] = [];
  let runText = '';
  let runColor: CellColor | null = null;
  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i] ?? ' ';
    const color = colors[i] ?? CELL_BLANK;
    if (runColor === null || color === runColor) {
      runText += ch;
      runColor = color;
    } else {
      segments.push({ text: runText, color: runColor });
      runText = ch;
      runColor = color;
    }
  }
  if (runColor !== null && runText.length > 0) {
    segments.push({ text: runText, color: runColor });
  }
  return segments;
}

/** The index of a lane's fork commit within its own `commits` (the first commit shared with main),
 * or `-1` for main / an unfound merge-base (the lane is then treated as wholly its own). Pure. */
function forkIndex(lane: TransitLane): number {
  if (lane.forkSha === null) {
    return -1;
  }
  return lane.commits.findIndex((c) => c.sha === lane.forkSha);
}

/** Display-ready geometry for one lane row. */
export interface TransitLaneView {
  readonly branch: string;
  readonly isMain: boolean;
  /** Single-letter hint key for `g`+hint lane jump. */
  readonly hint: string;
  readonly headSha: string;
  /** The lane's color index (its row index); the component maps it to the accent. */
  readonly colorIndex: number;
  /** The `▐ name ⌂ ▌` tag drawn in the right-hand tag column, in the lane's color. */
  readonly tag: string;
  /** The railway row as contiguous colored segments (`railwayWidth` cells total). */
  readonly segments: readonly RailSegment[];
  /** The shas of this lane's own visible stations, newest-first — for tests/diagnostics. */
  readonly stationShas: readonly string[];
}

/** The resolved selected commit, for the bottom info section. */
export interface TransitSelectedView {
  readonly sha: string;
  readonly short: string;
  readonly branch: string;
  readonly subject: string;
  readonly body: string;
  /** Floored coarse age, e.g. `"3h"`, `"2d"`. */
  readonly age: string;
}

/** The whole Git-Tree view, render-ready. */
export interface TransitView {
  readonly lanes: readonly TransitLaneView[];
  /** The shared age ruler (drawn above the lanes), `railwayWidth` wide. */
  readonly ruler: string;
  /** The railway region width (inner width minus the tag column + gap). */
  readonly railwayWidth: number;
  /** The right-hand tag column width (so the component aligns tags). */
  readonly tagColWidth: number;
  /** The main lane's color index (so the component paints it `active`, others the ring). */
  readonly mainIndex: number;
  readonly selected: TransitSelectedView | null;
  /** The wrapped commit-message lines for the selected commit (capped, deterministic count). */
  readonly infoLines: readonly string[];
  readonly status: TransitState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** The cursor the component owns: which lane + which commit sha is selected. */
export interface TransitCursor {
  readonly laneIndex: number;
  readonly sha: string | null;
}

/**
 * Assign each lane a unique single-letter HINT key (for `g`+hint → jump to that lane's HEAD). Prefer
 * the first free char of the branch name (lower-cased, a–z only); fall back to a digit, then to any
 * free a–z, so every lane gets a distinct key. Returns a `Map<branch, hint>`. Pure.
 */
export function assignLaneHints(lanes: readonly TransitLane[]): Map<string, string> {
  const used = new Set<string>();
  const hints = new Map<string, string>();
  const take = (candidate: string): boolean => {
    if (candidate.length === 0 || used.has(candidate)) {
      return false;
    }
    used.add(candidate);
    return true;
  };
  for (const lane of lanes) {
    let assigned: string | null = null;
    for (const raw of lane.branch.toLowerCase()) {
      if (raw >= 'a' && raw <= 'z' && take(raw)) {
        assigned = raw;
        break;
      }
    }
    if (assigned !== null) {
      hints.set(lane.branch, assigned);
    }
  }
  const digits = '0123456789';
  const alpha = 'abcdefghijklmnopqrstuvwxyz';
  for (let i = 0; i < lanes.length; i += 1) {
    const lane = lanes[i];
    if (lane === undefined || hints.has(lane.branch)) {
      continue;
    }
    let assigned: string | null = null;
    for (const d of digits) {
      if (take(d)) {
        assigned = d;
        break;
      }
    }
    if (assigned === null) {
      for (const a of alpha) {
        if (take(a)) {
          assigned = a;
          break;
        }
      }
    }
    hints.set(lane.branch, assigned ?? String(i));
  }
  return hints;
}

/**
 * Resolve a duration-jump on `lane`: find the commit whose `tsEpoch` is CLOSEST to `nowMs/1000 -
 * durationMs/1000`. Because a lane's commits include pre-fork shared ancestry, a large duration
 * resolves onto a commit shared with `main` — the component then maps that sha onto the main lane.
 * Returns the resolved `sha` (and its index in `lane.commits`), or null for an empty lane. Pure.
 */
export function resolveDurationJump(
  lane: TransitLane,
  durationMs: number,
  nowMs: number,
): { readonly sha: string; readonly index: number } | null {
  if (lane.commits.length === 0) {
    return null;
  }
  const targetSec = nowMs / 1000 - durationMs / 1000;
  let bestIndex = 0;
  let bestDelta = Number.POSITIVE_INFINITY;
  for (let i = 0; i < lane.commits.length; i += 1) {
    const commit = lane.commits[i];
    if (commit === undefined) {
      continue;
    }
    const delta = Math.abs(commit.tsEpoch - targetSec);
    if (delta < bestDelta) {
      bestDelta = delta;
      bestIndex = i;
    }
  }
  const best = lane.commits[bestIndex];
  if (best === undefined) {
    return null;
  }
  return { sha: best.sha, index: bestIndex };
}

/** Parse a duration token (`"5d"`, `"20m"`, `"2h"`, `"1w"`) into milliseconds, or null if it isn't
 * `<digits><m|h|d|w>`. Pure. */
export function parseDuration(token: string): number | null {
  const match = /^(\d+)([mhdw])$/.exec(token.trim());
  if (match === null) {
    return null;
  }
  const n = Number(match[1]);
  if (!Number.isFinite(n)) {
    return null;
  }
  const unit = match[2];
  const MINUTE = 60_000;
  switch (unit) {
    case 'm':
      return n * MINUTE;
    case 'h':
      return n * 60 * MINUTE;
    case 'd':
      return n * 24 * 60 * MINUTE;
    case 'w':
      return n * 7 * 24 * 60 * MINUTE;
    default:
      return null;
  }
}

/** Compute the tag-column width: the widest lane tag (each name clipped), floored at 0. Pure. */
export function tagColumnWidth(lanes: readonly TransitLane[]): number {
  let max = 0;
  for (const lane of lanes) {
    const w = branchTag(lane.branch).length;
    if (w > max) {
      max = w;
    }
  }
  return max;
}

/**
 * Lay out the whole DAG grid on the ORDINAL axis: reserve the right-hand tag column, {@link
 * packColumns} into time-rank slots, keep the NEWEST columns that fit at {@link COL_STRIDE} (oldest
 * clip off the left), then draw each lane's track + stations and overlay the branch fork connectors.
 * Returns the per-lane colored segment rows + the railway width. Pure — the heart of the geometry,
 * unit-testable without React.
 */
export function layoutDag(
  lanes: readonly TransitLane[],
  cursor: TransitCursor,
  innerWidth: number,
  nowMs: number,
): {
  laneRows: { segments: RailSegment[]; stationShas: string[] }[];
  ruler: string;
  railwayWidth: number;
  tagColWidth: number;
} {
  const tagColWidth = tagColumnWidth(lanes);
  const railwayWidth = Math.max(1, innerWidth - tagColWidth - TAG_GAP);
  const rows = lanes.length;

  // The cell grid: char + color per (row, col). Initialized blank.
  const chars: string[][] = Array.from({ length: rows }, () =>
    new Array<string>(railwayWidth).fill(' '),
  );
  const colors: CellColor[][] = Array.from({ length: rows }, () =>
    new Array<CellColor>(railwayWidth).fill(CELL_BLANK),
  );
  const set = (r: number, c: number, ch: string, color: CellColor): void => {
    if (r < 0 || r >= rows || c < 0 || c >= railwayWidth) {
      return;
    }
    const row = chars[r];
    const colorRow = colors[r];
    if (row === undefined || colorRow === undefined) {
      return;
    }
    row[c] = ch;
    colorRow[c] = color;
  };

  const mainIndex = Math.max(
    0,
    lanes.findIndex((l) => l.isMain),
  );

  // Pack into ordinal columns (newest→oldest); x is by RANK, not time. {@link planVisibleColumns}
  // then picks WHICH columns to show and where the dashed "jump" discontinuities fall: the newest
  // column plus a contiguous recent block, with every branch's far-back FORK vertex (and the cursor's
  // column) PINNED into view across a single dashed gap. So the `┳`/`╰` last-split junction always
  // shows even when it is many commits back, per the spec.
  const allColumns = packColumns(lanes, nowMs);
  const fit = Math.max(1, Math.floor((railwayWidth - 1) / COL_STRIDE) + 1);
  // The rank (in the full newest→oldest sequence) of each branch's fork column — the column whose
  // MAIN-row sha is the branch's forkSha (the merge-base lives on main's row) — and of the cursor.
  const rankOfMainSha = (sha: string): number =>
    allColumns.findIndex((col) => col.byLane.get(mainIndex) === sha);
  const forkRanks: number[] = [];
  for (const lane of lanes) {
    if (lane.forkSha !== null) {
      const fr = rankOfMainSha(lane.forkSha);
      if (fr >= 0) {
        forkRanks.push(fr);
      }
    }
  }
  const cursorRank =
    cursor.sha === null
      ? -1
      : allColumns.findIndex((col) => col.byLane.get(cursor.laneIndex) === cursor.sha);
  const slots = planVisibleColumns(allColumns, forkRanks, cursorRank, fit);
  const xOf = (i: number): number => railwayWidth - 1 - i * COL_STRIDE; // i=0 newest slot → right

  // Where each rank lands on the rail (its slot's x), for forks/tracks. -1 when the rank isn't shown.
  const xOfRank = (rank: number): number => {
    for (let i = 0; i < slots.length; i += 1) {
      const slot = slots[i];
      if (slot?.kind === 'col' && slot.rank === rank) {
        return xOf(i);
      }
    }
    return -1;
  };
  const forkColumnX = (lane: TransitLane): number | null => {
    if (lane.forkSha === null) {
      return null;
    }
    const x = xOfRank(rankOfMainSha(lane.forkSha));
    return x < 0 ? null : x;
  };

  // The dashed "jump" cell ranges: the cells strictly between a gap slot's two neighbours' anchors.
  // A track crossing one of these ranges is later redrawn dashed (the discontinuity), so a branch line
  // visibly "jumps" the elided commits back to its fork vertex while staying continuous either side.
  const gapSpans: { lo: number; hi: number }[] = [];
  for (let i = 0; i < slots.length; i += 1) {
    if (slots[i]?.kind !== 'gap') {
      continue;
    }
    const xNewer = xOf(i - 1); // the col slot to the right (newer)
    const xOlder = xOf(i + 1); // the col slot to the left (older)
    gapSpans.push({ lo: xOlder + 1, hi: xNewer - 1 });
  }
  const inGap = (c: number): boolean => gapSpans.some((g) => c >= g.lo && c <= g.hi);

  // Pass A — each lane's track + stations (a station per visible column the lane occupies).
  const stationShasByRow: string[][] = lanes.map(() => []);
  for (let r = 0; r < rows; r += 1) {
    const lane = lanes[r];
    if (lane === undefined) {
      continue;
    }
    const stations: { x: number; sha: string }[] = [];
    for (let i = 0; i < slots.length; i += 1) {
      const slot = slots[i];
      if (slot?.kind !== 'col') {
        continue;
      }
      const sha = slot.column.byLane.get(r);
      if (sha !== undefined) {
        stations.push({ x: xOf(i), sha });
      }
    }
    if (stations.length === 0) {
      continue;
    }
    const xs = stations.map((s) => s.x);
    // Extend the track left to the fork column so the branch line reaches its fork tee on main.
    const forkX = forkColumnX(lane);
    const startTrack = Math.min(...xs, forkX ?? Number.POSITIVE_INFINITY);
    const endTrack = Math.max(...xs);
    for (let c = startTrack; c <= endTrack; c += 1) {
      set(r, c, inGap(c) ? TRACK_GAP : TRACK, r);
    }
    for (const station of stations) {
      const isSelected = r === cursor.laneIndex && station.sha === cursor.sha;
      const isHead = station.sha === lane.headSha;
      const glyph = isSelected ? SELECTED_GLYPH : isHead ? HEAD_CAP : STATION;
      set(r, station.x, glyph, isSelected ? CELL_SELECTED : r);
      stationShasByRow[r]?.push(station.sha);
    }
  }

  // Pass B — branch fork connectors (drawn after tracks so the tee/corner win their cells).
  for (let r = 0; r < rows; r += 1) {
    const lane = lanes[r];
    if (lane === undefined || lane.forkSha === null) {
      continue;
    }
    const fx = forkColumnX(lane);
    if (fx === null) {
      continue;
    }
    // The corner where this branch's own track begins, turning up toward main.
    set(r, fx, CONNECTOR_CORNER, r);
    // Vertical through any rows strictly between main and this branch (only over blanks, so a real
    // station on an intervening lane keeps its glyph — the line crosses behind it).
    const lo = Math.min(mainIndex, r);
    const hi = Math.max(mainIndex, r);
    for (let row = lo + 1; row < hi; row += 1) {
      const colorRow = colors[row];
      if (colorRow !== undefined && colorRow[fx] === CELL_BLANK) {
        set(row, fx, CONNECTOR_VERT, r);
      }
    }
    // The tee on main's row (in the branch's color).
    set(mainIndex, fx, STATION_FORK, r);
  }

  const laneRows = lanes.map((_, r) => ({
    segments: rowToSegments(chars[r] ?? [], colors[r] ?? []),
    stationShas: stationShasByRow[r] ?? [],
  }));

  return {
    laneRows,
    ruler: buildColumnRuler(slots, xOf, railwayWidth),
    railwayWidth,
    tagColWidth,
  };
}

/** Pure transform: lanes + cursor + inner width → display-ready view. `now` is injected (testable). */
export function selectTransitView(
  state: TransitState,
  cursor: TransitCursor,
  innerWidth: number,
  nowMs: number,
): TransitView {
  const hints = assignLaneHints(state.lanes);
  const { laneRows, ruler, railwayWidth, tagColWidth } = layoutDag(
    state.lanes,
    cursor,
    innerWidth,
    nowMs,
  );
  const mainIndex = Math.max(
    0,
    state.lanes.findIndex((l) => l.isMain),
  );

  const lanes: TransitLaneView[] = state.lanes.map((lane, idx) => ({
    branch: lane.branch,
    isMain: lane.isMain,
    hint: hints.get(lane.branch) ?? '?',
    headSha: lane.headSha,
    colorIndex: idx,
    tag: branchTag(lane.branch),
    segments: laneRows[idx]?.segments ?? [],
    stationShas: laneRows[idx]?.stationShas ?? [],
  }));

  // Resolve the selected commit for the info section: prefer the cursor's lane, then any lane that
  // contains the sha (pre-fork shared commits live on main too).
  let selected: TransitSelectedView | null = null;
  if (cursor.sha !== null) {
    const order = [
      ...(state.lanes[cursor.laneIndex] !== undefined ? [state.lanes[cursor.laneIndex]] : []),
      ...state.lanes,
    ];
    for (const lane of order) {
      if (lane === undefined) {
        continue;
      }
      const commit = lane.commits.find((c) => c.sha === cursor.sha);
      if (commit !== undefined) {
        selected = {
          sha: commit.sha,
          short: commit.short,
          branch: lane.branch,
          subject: commit.subject,
          body: commit.body,
          age: floorAge(commit.tsEpoch, nowMs),
        };
        break;
      }
    }
  }

  // The wrapped message: subject then body, wrapped to the full inner width, capped to the reserve.
  let infoLines: string[] = [];
  if (selected !== null) {
    const wrapped = [
      ...wrapText(selected.subject, innerWidth),
      ...wrapText(selected.body, innerWidth),
    ];
    infoLines = wrapped.slice(0, INFO_BODY_LINES);
  }

  return {
    lanes,
    ruler,
    railwayWidth,
    tagColWidth,
    mainIndex,
    selected,
    infoLines,
    status: state.status,
    error: state.error,
    isEmpty: state.lanes.length === 0,
  };
}

/**
 * Component-facing hook: memoises {@link selectTransitView} on the slice + cursor + inner width, with
 * `now` bucketed to the minute so ages tick without recomputing on every render.
 */
export function useTransitView(
  state: TransitState,
  cursor: TransitCursor,
  innerWidth: number,
): TransitView {
  const nowBucket = Math.floor(Date.now() / 60000);
  return useMemo(
    () => selectTransitView(state, cursor, innerWidth, nowBucket * 60000),
    [state, cursor, innerWidth, nowBucket],
  );
}
