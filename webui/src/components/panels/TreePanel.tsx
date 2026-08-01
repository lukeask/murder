/**
 * TreePanel — git commit tree from the `transit` slice: lanes with commits.
 * Keyboard transit nav (TUI parity): hjkl lane/commit cursor, `g` duration/hint jump.
 * Cursor + g-buffer persist via paneUiStore in the composer bundle.
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import {
  parseDuration,
  resolveDurationJump,
  useTransitView,
  type TransitCursor,
} from '@murder/ui-core/selectors/transitSelectors.js';
import type { TransitLane } from '@murder/ui-core/store/transit/transitSlice.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { usePaneGBuffer } from '../../composer/usePaneGBuffer.js';
import { usePaneTransitCursor } from '../../composer/usePaneTransitCursor.js';
import { panelFocusStore, useIsPanelFocused } from '../../panelFocus.js';
import { Panel } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

const TREE_PANE_ID = 'tree';
/** Width for useTransitView layout (railway unused in web list; still drives selected/hints). */
const TRANSIT_VIEW_WIDTH = 80;
const TREE_UNIT_LETTERS = new Set(['m', 'h', 'd', 'w']);

/** The crow identity palette has 6 slots; cycle lanes through them. */
const CROW_SLOTS = 6;

function nearestShaByTime(
  lane: { readonly commits: readonly { readonly sha: string; readonly tsEpoch: number }[] },
  tsEpoch: number | null,
): string | null {
  if (lane.commits.length === 0) return null;
  if (tsEpoch === null) return lane.commits[0]?.sha ?? null;
  let best = lane.commits[0];
  let bestDelta = Number.POSITIVE_INFINITY;
  for (const commit of lane.commits) {
    const delta = Math.abs(commit.tsEpoch - tsEpoch);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = commit;
    }
  }
  return best?.sha ?? null;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.closest('[data-terminal-input="true"]') !== null) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable;
}

export function TreePanel(): React.JSX.Element {
  const transit = useAppStore((s) => s.transit, shallow);
  const focused = useIsPanelFocused('tree');
  const [cursor, setCursor] = usePaneTransitCursor(TREE_PANE_ID, transit.lanes.length);
  const [gBuffer, setGBuffer] = usePaneGBuffer(TREE_PANE_ID);
  const gPending = gBuffer !== null;
  const view = useTransitView(transit, cursor, TRANSIT_VIEW_WIDTH);
  const isEmpty = transit.lanes.length === 0;

  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const gBufferRef = useRef(gBuffer);
  gBufferRef.current = gBuffer;
  const lanesRef = useRef(transit.lanes);
  lanesRef.current = transit.lanes;
  const viewLanesRef = useRef(view.lanes);
  viewLanesRef.current = view.lanes;

  useEffect(() => {
    if (cursor.sha === null && transit.lanes.length > 0) {
      const head = transit.lanes[0]?.headSha ?? null;
      if (head !== null) {
        setCursor({ laneIndex: 0, sha: head });
      }
    }
  }, [cursor.sha, transit.lanes, setCursor]);

  const moveWithinLane = useCallback(
    (delta: number) => {
      const c = cursorRef.current;
      const lane = lanesRef.current[c.laneIndex];
      if (lane === undefined || lane.commits.length === 0) return;
      const index = lane.commits.findIndex((commit) => commit.sha === c.sha);
      const current = index >= 0 ? index : 0;
      const next = Math.min(Math.max(current + delta, 0), lane.commits.length - 1);
      const sha = lane.commits[next]?.sha ?? null;
      setCursor((cur) => ({ ...cur, sha }));
    },
    [setCursor],
  );

  const switchLane = useCallback(
    (delta: number) => {
      const lanes = lanesRef.current;
      if (lanes.length === 0) return;
      const c = cursorRef.current;
      const nextIndex = Math.min(Math.max(c.laneIndex + delta, 0), lanes.length - 1);
      if (nextIndex === c.laneIndex) return;
      const currentLane = lanes[c.laneIndex];
      const currentTs =
        currentLane?.commits.find((commit) => commit.sha === c.sha)?.tsEpoch ?? null;
      const nextLane = lanes[nextIndex];
      if (nextLane === undefined) return;
      setCursor({ laneIndex: nextIndex, sha: nearestShaByTime(nextLane, currentTs) });
    },
    [setCursor],
  );

  const jumpToSha = useCallback(
    (sha: string) => {
      const lanes = lanesRef.current;
      const mainIndex = lanes.findIndex((lane) => lane.isMain);
      const mainLane = mainIndex >= 0 ? lanes[mainIndex] : undefined;
      if (mainLane?.commits.some((commit) => commit.sha === sha)) {
        setCursor({ laneIndex: mainIndex, sha });
        return;
      }
      setCursor((current) => ({ ...current, sha }));
    },
    [setCursor],
  );

  const handleGChar = useCallback(
    (ch: string) => {
      const buffer = gBufferRef.current ?? '';
      if (buffer.length === 0) {
        const laneByHint = viewLanesRef.current.find((lane) => lane.hint === ch);
        if (laneByHint !== undefined) {
          const lane = lanesRef.current.find((candidate) => candidate.branch === laneByHint.branch);
          if (lane !== undefined) {
            const laneIndex = lanesRef.current.indexOf(lane);
            setCursor({ laneIndex, sha: lane.headSha });
          }
          gBufferRef.current = null;
          setGBuffer(null);
          return;
        }
      }
      if (ch >= '0' && ch <= '9') {
        const next = buffer + ch;
        gBufferRef.current = next;
        setGBuffer(next);
        return;
      }
      if (TREE_UNIT_LETTERS.has(ch)) {
        const next = buffer + ch;
        gBufferRef.current = next;
        setGBuffer(next);
      }
    },
    [setCursor, setGBuffer],
  );

  const resolveG = useCallback(() => {
    const buffer = gBufferRef.current ?? '';
    const ms = parseDuration(buffer);
    const selectedLane = lanesRef.current[cursorRef.current.laneIndex] ?? null;
    if (ms !== null && selectedLane !== null) {
      const resolved = resolveDurationJump(selectedLane, ms, Date.now());
      if (resolved !== null) {
        jumpToSha(resolved.sha);
      }
    }
    gBufferRef.current = null;
    setGBuffer(null);
  }, [jumpToSha, setGBuffer]);

  const selectCommit = useCallback(
    (laneIndex: number, sha: string) => {
      setCursor({ laneIndex, sha });
      setGBuffer(null);
    },
    [setCursor, setGBuffer],
  );

  useTreeKeyboard({
    active: focused,
    gPending,
    moveWithinLane,
    switchLane,
    handleGChar,
    resolveG,
    setGBuffer,
  });

  // Scroll the cursor commit into view when navigating by keyboard.
  useEffect(() => {
    if (!focused || cursor.sha === null) return;
    const el = document.querySelector(`.transit-commit[data-sha="${cursor.sha}"]`);
    if (el !== null && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' });
    }
  }, [focused, cursor.sha, cursor.laneIndex]);

  const selected = view.selected;

  return (
    <Panel
      title="Git Tree"
      count={isEmpty ? null : transit.lanes.length}
      data-panel-id="tree"
      active={focused}
      onHeaderClick={() => panelFocusStore.getState().focus('tree')}
    >
      <SliceHint
        state={{ status: transit.status, error: transit.error, isEmpty }}
        empty="No branches."
      />
      {transit.lanes.map((lane, laneIdx) => (
        <TransitLaneBlock
          key={lane.branch}
          lane={lane}
          laneIdx={laneIdx}
          cursor={cursor}
          onSelect={selectCommit}
        />
      ))}
      {selected !== null ? (
        <div className="transit-detail">
          <div className="transit-detail__head">
            <span className="transit-detail__sha">{selected.short}</span>
            <span className="transit-detail__subject">{selected.subject}</span>
          </div>
          {selected.body.length > 0 ? (
            <pre className="transit-detail__body">{selected.body}</pre>
          ) : null}
        </div>
      ) : null}
      {gPending ? (
        <div className="mds-goto transit-jump" role="status" aria-live="polite">
          <span className="mds-goto__chord">g</span>
          <span className="mds-goto__digits">
            {gBuffer !== null && gBuffer.length > 0 ? gBuffer : '…'}
          </span>
          <span className="transit-jump__hint">
            {view.lanes.map((l) => `[${l.hint}]${l.branch}`).join(' · ') || '5d/20m ⏎'}
          </span>
        </div>
      ) : null}
    </Panel>
  );
}

function TransitLaneBlock({
  lane,
  laneIdx,
  cursor,
  onSelect,
}: {
  readonly lane: TransitLane;
  readonly laneIdx: number;
  readonly cursor: TransitCursor;
  readonly onSelect: (laneIndex: number, sha: string) => void;
}): React.JSX.Element {
  const laneColor = `var(--crow-${(laneIdx % CROW_SLOTS) + 1})`;
  const laneSelected = laneIdx === cursor.laneIndex;
  const now = Date.now();

  return (
    <div
      className="transit-lane"
      data-cursor-lane={laneSelected ? 'true' : undefined}
      style={{ '--lane-color': laneColor } as React.CSSProperties}
    >
      <div className="transit-lane__branch">
        <span className="transit-lane__dot" />
        <span
          className={
            lane.isMain ? 'transit-lane__name transit-lane__name--main' : 'transit-lane__name'
          }
        >
          {lane.branch}
        </span>
        {lane.isMain ? <span className="transit-lane__home">⌂</span> : null}
      </div>
      <ul className="transit-lane__commits">
        {lane.commits.map((c) => (
          <li
            key={c.sha}
            className="transit-commit"
            data-sha={c.sha}
            data-selected={c.sha === cursor.sha && laneSelected ? 'true' : undefined}
            onClick={() => onSelect(laneIdx, c.sha)}
          >
            <span className="transit-commit__node" />
            <span className="transit-commit__sha">{c.short}</span>
            <span className="transit-commit__subject">{c.subject}</span>
            <span className="transit-commit__age">{ageLabel(c.tsEpoch, now)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ageLabel(tsEpochSec: number, nowMs: number): string {
  const deltaSec = Math.max(0, Math.floor(nowMs / 1000 - tsEpochSec));
  if (deltaSec < 60) return 'now';
  const m = Math.floor(deltaSec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  return `${Math.floor(d / 7)}w`;
}

function useTreeKeyboard({
  active,
  gPending,
  moveWithinLane,
  switchLane,
  handleGChar,
  resolveG,
  setGBuffer,
}: {
  readonly active: boolean;
  readonly gPending: boolean;
  readonly moveWithinLane: (delta: number) => void;
  readonly switchLane: (delta: number) => void;
  readonly handleGChar: (ch: string) => void;
  readonly resolveG: () => void;
  readonly setGBuffer: Dispatch<SetStateAction<string | null>>;
}): void {
  const gPendingRef = useRef(gPending);
  gPendingRef.current = gPending;
  const moveRef = useRef(moveWithinLane);
  moveRef.current = moveWithinLane;
  const switchRef = useRef(switchLane);
  switchRef.current = switchLane;
  const gCharRef = useRef(handleGChar);
  gCharRef.current = handleGChar;
  const resolveRef = useRef(resolveG);
  resolveRef.current = resolveG;
  const setGRef = useRef(setGBuffer);
  setGRef.current = setGBuffer;

  useEffect(() => {
    if (!active) return;

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.defaultPrevented || e.repeat) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;

      const pending = gPendingRef.current;
      const key = e.key;

      if (key === 'Escape') {
        e.preventDefault();
        if (pending) {
          gPendingRef.current = false;
          setGRef.current(null);
        } else {
          panelFocusStore.getState().clear();
        }
        return;
      }

      if (key === 'Enter') {
        if (pending) {
          e.preventDefault();
          gPendingRef.current = false;
          resolveRef.current();
        }
        return;
      }

      if (key === 'g' && !pending) {
        e.preventDefault();
        gPendingRef.current = true;
        setGRef.current('');
        return;
      }

      if (key === 'h' || key === 'ArrowLeft') {
        e.preventDefault();
        if (pending) gCharRef.current('h');
        else moveRef.current(1);
        return;
      }
      if (key === 'l' || key === 'ArrowRight') {
        if (pending) return;
        e.preventDefault();
        moveRef.current(-1);
        return;
      }
      if (key === 'j' || key === 'ArrowDown') {
        if (pending) return;
        e.preventDefault();
        switchRef.current(1);
        return;
      }
      if (key === 'k' || key === 'ArrowUp') {
        if (pending) return;
        e.preventDefault();
        switchRef.current(-1);
        return;
      }

      if (pending && key.length === 1) {
        e.preventDefault();
        gCharRef.current(key.toLowerCase());
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [active]);
}
