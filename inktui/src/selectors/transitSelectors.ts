/**
 * Transit view-models — the selector (rule 2: ALL formatting/geometry lives here, never in the
 * store or the component). Every function in this file is PURE (no React, no Ink) and computes
 * deterministic line/station counts — NEVER relying on `measureElement` for wrapped text (memory
 * `project_inktui_measure_wrap`): the railway line and the age-marker line are built character-by-
 * character to a known inner width so Yoga never disagrees about their height.
 *
 * Two layers like every selector: pure transforms (exported for unit tests) + a memoised
 * {@link useTransitView} hook bucketed on a minute `now` so ages tick without re-running per render.
 *
 * ## Railway geometry
 * A lane's commits are newest-first. We pick how many STATIONS fit the inner width (each station is
 * one glyph + a fixed run of `━` track between them), then build:
 *  - the railway string: `▶` (newest end / HEAD cap) … `◉`/`○`/`●` stations joined by `━` track, with
 *    the SELECTED station highlighted (`◉`). Fork points within the window draw `┳`.
 *  - the age-marker line: the same width, with a sparse floored-age label aligned UNDER each station.
 */

import { useMemo } from 'react';
import type { TransitLane, TransitState } from '../store/transit/transitSlice.js';

// ── Glyphs (from the `7-the-depot.ans` mockup) ────────────────────────────────────────────────────
/** The newest-end cap drawn at the right edge of a lane (HEAD direction). */
const HEAD_CAP = '▶';
/** A normal commit station. */
const STATION = '○';
/** The selected commit station (highlighted). */
const STATION_SELECTED = '◉';
/** A fork/merge station (a lane crossing — fork commit within the window). */
const STATION_FORK = '┳';
/** One cell of horizontal track between stations. */
const TRACK = '━';
/** Cells of track drawn between adjacent stations. Stations are `1 + TRACK_RUN` cells apart. */
export const TRACK_RUN = 3;
/** Cells one station block consumes on the railway line: the glyph + its trailing track run. */
export const STATION_STRIDE = 1 + TRACK_RUN;

/** How many station blocks fit in `innerWidth` cells (at least 1 when there's any width). The newest
 * station also needs its glyph cell, so the count is `floor((width) / stride)` floored at 1. Pure. */
export function stationsThatFit(innerWidth: number): number {
  if (innerWidth <= 0) {
    return 0;
  }
  return Math.max(1, Math.floor(innerWidth / STATION_STRIDE));
}

/**
 * Floor a commit's age (unix epoch SECONDS) against `nowMs` to a coarse label, rounding DOWN:
 *  - `< 60m` → `"Nm"`, `< 24h` → `"Nh"`, `< 7d` → `"Nd"`, else `"Nw"`.
 * A future/negative delta floors to `"0m"`. Pure — the marker placement walks these.
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

/**
 * Sparse age-marker placement. Walking the visible stations NEWEST→OLDEST, emit a label under a
 * station only when its floored value DIFFERS from the last emitted one (so a 1h05m station emits
 * `"1h"`, the next still-1h station emits `null`, and the first 2h station emits `"2h"`). The result
 * aligns 1:1 with `visibleStationsNewestFirst` (same length/order). Pure.
 */
export function placeAgeMarkers(
  visibleStationsNewestFirst: readonly { readonly tsEpoch: number }[],
  nowMs: number,
): (string | null)[] {
  const out: (string | null)[] = [];
  let lastEmitted: string | null = null;
  for (const station of visibleStationsNewestFirst) {
    const label = floorAge(station.tsEpoch, nowMs);
    if (label !== lastEmitted) {
      out.push(label);
      lastEmitted = label;
    } else {
      out.push(null);
    }
  }
  return out;
}

/** The ~2-station left margin kept ahead of the selection when windowing the selected lane. */
export const SELECTED_LEFT_MARGIN = 2;

/**
 * Choose the visible window of commit indices (into `lane.commits`, which is newest-first) for a
 * lane at a given `innerWidth`.
 *
 * Non-selected lanes (or no/absent selection) show the NEWEST-fitting commits: indices `0..count-1`.
 *
 * The SELECTED lane windows so the selected station stays visible while keeping a ~2-station LEFT
 * margin (`SELECTED_LEFT_MARGIN`) — i.e. up to 2 newer commits remain visible to the selection's
 * right. As you move OLDER (h) past the newest-fitting window the window scrolls back: oldest
 * stations truncate on the left normally; once scrolled back, the newest truncate on the right.
 *
 * Returns the array of commit indices in NEWEST-FIRST order (so index 0 is the newest VISIBLE commit).
 * Pure — unit-testable for the margin rule.
 */
export function windowIndices(
  commitCount: number,
  count: number,
  selectedIndex: number | null,
): number[] {
  const fit = Math.min(count, commitCount);
  if (fit <= 0) {
    return [];
  }
  // Default (non-selected / no selection): newest-fitting, indices 0..fit-1.
  let start = 0;
  if (selectedIndex !== null && selectedIndex >= fit) {
    // The selection is older than the newest-fitting window — scroll back so the selection sits
    // `SELECTED_LEFT_MARGIN` from the NEWEST visible edge (i.e. up to 2 newer commits stay on screen
    // to its right). Clamp so we never scroll past the oldest commit.
    const desiredStart = selectedIndex - SELECTED_LEFT_MARGIN;
    const maxStart = commitCount - fit;
    start = Math.max(0, Math.min(desiredStart, maxStart));
  }
  const indices: number[] = [];
  for (let i = 0; i < fit; i += 1) {
    indices.push(start + i);
  }
  return indices;
}

/** Display-ready geometry for one lane. */
export interface LaneLayout {
  /** The railway glyph string (newest on the RIGHT, oldest on the LEFT), padded to inner width. */
  readonly railwayLine: string;
  /** The position-aligned sparse age-marker line, same width as `railwayLine`. */
  readonly ageLine: string;
  /** The shas of the visible stations, in LEFT→RIGHT order (oldest→newest) — what the railway draws. */
  readonly stationShas: string[];
}

/**
 * Lay out one lane's railway + age line for `innerWidth`, honouring the selected-lane windowing.
 * `selectedSha` is the cursor's commit on THIS lane (or null when this isn't the selected lane).
 *
 * The railway is drawn newest-on-the-RIGHT (matching the mockup's `…◉━━━●━━━▶`). Internally we window
 * newest-first, then reverse to paint oldest→newest left→right. The age line places a sparse floored
 * label under each station (newest→oldest emission, see {@link placeAgeMarkers}), aligned to the same
 * column each station occupies on the railway. Deterministic widths only. Pure.
 */
export function layoutLane(
  lane: TransitLane,
  innerWidth: number,
  selectedSha: string | null,
  nowMs: number,
): LaneLayout {
  const count = stationsThatFit(innerWidth);
  const selectedIndex =
    selectedSha === null ? null : lane.commits.findIndex((c) => c.sha === selectedSha);
  const normalizedSelected = selectedIndex !== null && selectedIndex >= 0 ? selectedIndex : null;
  const idxNewestFirst = windowIndices(lane.commits.length, count, normalizedSelected);
  const visibleNewestFirst = idxNewestFirst
    .map((i) => lane.commits[i])
    .filter((c) => c !== undefined);

  // Age markers are emitted newest→oldest, aligned 1:1 with the newest-first station order.
  const markersNewestFirst = placeAgeMarkers(
    visibleNewestFirst.map((c) => ({ tsEpoch: c.tsEpoch })),
    nowMs,
  );

  // Paint oldest→newest left→right (reverse the newest-first window).
  const visibleLeftToRight = [...visibleNewestFirst].reverse();
  const markersLeftToRight = [...markersNewestFirst].reverse();
  const stationShas = visibleLeftToRight.map((c) => c.sha);

  // Build the railway + age line cell-by-cell. Each station occupies STATION_STRIDE columns: its
  // glyph + TRACK_RUN track cells. The newest station gets the HEAD_CAP appended after its glyph.
  let railway = '';
  let ages = '';
  const lastIdx = visibleLeftToRight.length - 1;
  for (let i = 0; i < visibleLeftToRight.length; i += 1) {
    const commit = visibleLeftToRight[i];
    if (commit === undefined) {
      continue;
    }
    const isSelected = selectedSha !== null && commit.sha === selectedSha;
    const isFork = lane.forkSha !== null && commit.sha === lane.forkSha;
    const glyph = isSelected ? STATION_SELECTED : isFork ? STATION_FORK : STATION;
    railway += glyph;
    // The age label aligns under the station glyph column.
    const label = markersLeftToRight[i] ?? null;
    ages += label !== null ? label : ' ';
    // Pad both lines to the station stride (track on the railway, spaces under it on the age line),
    // accounting for any age label that already consumed columns.
    const consumedByLabel = label !== null ? label.length : 0;
    const trackCells = i === lastIdx ? 0 : TRACK_RUN;
    railway += TRACK.repeat(trackCells);
    // This station block is `1 (glyph) + trackCells` cells wide on the railway; the age line must
    // consume the SAME width so labels stay column-aligned under their station. The label already
    // ate `consumedByLabel` cells, so pad the remainder (clamped at 0 when a multi-char label
    // overflows a narrow/last block — it spills into the next column, acceptable at the right edge).
    const ageFill = Math.max(0, 1 + trackCells - consumedByLabel);
    ages += ' '.repeat(ageFill);
  }
  // The newest (rightmost) station is the HEAD: cap it.
  railway += HEAD_CAP;
  ages += ' ';

  return { railwayLine: railway, ageLine: ages, stationShas };
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

/** Parse a duration token (`"5d"`, `"20m"`, `"20d"`, `"2h"`, `"1w"`) into milliseconds, or null if it
 * isn't `<digits><m|h|d|w>`. Pure. */
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
  // First pass: first free a–z char of the branch name.
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
  // Second pass: lanes still without a hint get the next free digit, then any free a–z, then index.
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

/** One lane, render-ready for the panel. */
export interface TransitLaneView {
  readonly branch: string;
  readonly isMain: boolean;
  /** Single-letter hint key for `g`+hint lane jump. */
  readonly hint: string;
  readonly headSha: string;
  /** The branch tag line content (e.g. `main ⌂`). */
  readonly branchTag: string;
  readonly railwayLine: string;
  readonly ageLine: string;
  readonly stationShas: string[];
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

/** The whole transit view, render-ready. */
export interface TransitView {
  readonly lanes: readonly TransitLaneView[];
  readonly selected: TransitSelectedView | null;
  readonly status: TransitState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** The cursor the component owns: which lane + which commit sha is selected. */
export interface TransitCursor {
  readonly laneIndex: number;
  readonly sha: string | null;
}

/** Pure transform: lanes + cursor + inner width → display-ready view. `now` is injected (testable). */
export function selectTransitView(
  state: TransitState,
  cursor: TransitCursor,
  innerWidth: number,
  nowMs: number,
): TransitView {
  const hints = assignLaneHints(state.lanes);
  const lanes: TransitLaneView[] = state.lanes.map((lane, idx) => {
    const isSelectedLane = idx === cursor.laneIndex;
    const selectedSha = isSelectedLane ? cursor.sha : null;
    const layout = layoutLane(lane, innerWidth, selectedSha, nowMs);
    return {
      branch: lane.branch,
      isMain: lane.isMain,
      hint: hints.get(lane.branch) ?? '?',
      headSha: lane.headSha,
      branchTag: `${lane.branch} ⌂`,
      railwayLine: layout.railwayLine,
      ageLine: layout.ageLine,
      stationShas: layout.stationShas,
    };
  });

  // Resolve the selected commit for the info section: find the cursor sha across lanes (the selected
  // lane first, then any lane that contains the sha — pre-fork shared commits live on main too).
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

  return {
    lanes,
    selected,
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
