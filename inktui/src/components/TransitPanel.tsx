/**
 * TransitPanel — the "Git Tree" commit-graph right-rail panel (panel 8, ctrl+8).
 *
 * Custom {@link ./Pane.tsx Pane} body (like {@link ./UsagePanel.tsx}, NOT a Ledger). The selector
 * renders a SWIMLANE DAG on a shared log-time axis: a single age RULER on top, then one row per
 * branch (railway as colored run-length segments + a right-hand `▐ name ⌂ ▌` tag in the lane color,
 * with fork connectors tying branches down to main), then a FIXED-height info section for the
 * selected commit (sha · branch · age, then the wrapped commit message). All geometry/colour is the
 * selector's (rule 2); this component owns only the cursor + the `g`-jump capture buffer (panel-local
 * `useState`, mirroring the doc-pane pending pattern — intentionally NOT a named chord in
 * `bindings.ts`). The SELECTED commit is a distinct glyph in the focus colour ("where you are").
 *
 * Row/segment counts are deterministic (the selector builds each row to the known inner width and
 * pre-wraps the message), so this never relies on `measureElement` for wrapped text (the measure-wrap
 * trap). Every row Box sets `flexShrink={0}` so Yoga never drops a line.
 *
 * ## The g-jump capture (panel-local)
 * `g` enters `gPending`; while pending the bottom strip shows the lane-hint keys + `type 5d/20m +⏎`.
 * The next key is interpreted locally:
 *  - a LANE-HINT letter → jump to that lane's HEAD (cursor = lane head sha);
 *  - a DIGIT → append to the duration buffer; a unit letter `m/h/d/w` → append the unit;
 *  - `⏎` → parse the buffer as a duration and `resolveDurationJump` on the selected lane, landing the
 *    cursor on the closest commit (mapping onto the main lane when the resolved sha is pre-fork);
 *  - `esc` → cancel.
 * Because lane hints and duration units overlap the printable-char space, the keymap registers one
 * chord per a–z letter and 0–9 digit (intent `char:<x>`) plus `g`/`enter`/`esc`/hjkl; the handler
 * branches on whether `gPending` is active. This keeps ALL key handling in the declared keymap (rule
 * 5) — the panel never calls `useInput`.
 */

import { Box, Text } from 'ink';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { KeymapEntry, PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';
import {
  CELL_BLANK,
  CELL_SELECTED,
  type CellColor,
  parseDuration,
  type RailSegment,
  resolveDurationJump,
  TAG_GAP,
  type TransitCursor,
  type TransitLaneView,
  type TransitView,
  useTransitView,
} from '../selectors/transitSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'transit';
const PANEL_TITLE = 'Git Tree';

/** The inner width assumed when mounted bare (a test rendering the panel outside the Rail). */
const DEFAULT_INNER_WIDTH = 38;

/** Map a cell's {@link CellColor} to a concrete hex: blank → undefined (uncolored), selected → the
 * focus/border color ("where you are"), else a LANE color (main = `active`, other branches cycle the
 * `laneColors` ring so each branch reads as its own accent). */
function colorForCell(
  color: CellColor,
  mainIndex: number,
  theme: ReturnType<typeof useTheme>,
): string | undefined {
  if (color === CELL_BLANK) {
    return undefined;
  }
  if (color === CELL_SELECTED) {
    return theme.focus;
  }
  return laneColor(color, mainIndex, theme);
}

/** A lane's accent: main owns the `active` green; every other branch takes a distinct color from the
 * `laneColors` ring (keyed by its index so the assignment is stable). */
function laneColor(index: number, mainIndex: number, theme: ReturnType<typeof useTheme>): string {
  if (index === mainIndex) {
    return theme.active;
  }
  const ring = theme.laneColors;
  return ring[index % ring.length] ?? theme.text;
}

/** The panel's intent union: navigation + `g`-mode control + a per-char family (`char:<x>`). */
type TransitIntent =
  | 'older'
  | 'newer'
  | 'laneDown'
  | 'laneUp'
  | 'startG'
  | 'gEnter'
  | 'gEsc'
  | `char:${string}`;

const ALPHA = 'abcdefghijklmnopqrstuvwxyz';
const DIGITS = '0123456789';
/** Unit letters that extend a duration buffer while in `gPending`. */
const UNIT_LETTERS = new Set(['m', 'h', 'd', 'w']);

/** One lane ROW: the railway (colored segments on the shared time axis) followed by the lane's
 * `▐ name ⌂ ▌` tag in the lane color (bold + a selection band when this is the cursor's lane). A
 * single `flexShrink={0}` Text so Yoga never drops the line, and `wrap="truncate"` clamps it to the
 * rail width. The selected COMMIT is colored within the segments (the {@link CELL_SELECTED} run). */
function LaneRow({
  lane,
  selected,
  mainIndex,
  theme,
}: {
  readonly lane: TransitLaneView;
  readonly selected: boolean;
  readonly mainIndex: number;
  readonly theme: ReturnType<typeof useTheme>;
}): React.JSX.Element {
  const tagAccent = laneColor(lane.colorIndex, mainIndex, theme);
  return (
    <Box flexShrink={0}>
      <Text wrap="truncate">
        {lane.segments.map((seg: RailSegment, i: number) => {
          const cellColor = colorForCell(seg.color, mainIndex, theme);
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: segments are a positional run-length list of the row; the index IS the stable identity.
            <Text key={`seg-${i}`} {...(cellColor !== undefined ? { color: cellColor } : {})}>
              {seg.text}
            </Text>
          );
        })}
        {' '.repeat(TAG_GAP)}
        <Text
          color={tagAccent}
          bold={selected}
          {...(selected ? { backgroundColor: theme.panelSelectedBg } : {})}
        >
          {lane.tag}
        </Text>
      </Text>
    </Box>
  );
}

/** The fixed-height info section for the selected commit, OR the g-hint overlay when `gPending`. The
 * sha line is followed by the pre-WRAPPED commit message (subject + body) the selector produced —
 * deterministic line count, so no `measureElement` (the measure-wrap trap). */
function InfoSection({
  view,
  gPending,
  gBuffer,
  theme,
}: {
  readonly view: TransitView;
  readonly gPending: boolean;
  readonly gBuffer: string;
  readonly theme: ReturnType<typeof useTheme>;
}): React.JSX.Element {
  if (gPending) {
    const hints = view.lanes.map((l) => `[${l.hint}] ${l.branch}`).join('  ');
    return (
      <Box flexDirection="column" flexShrink={0}>
        <Box flexShrink={0}>
          <Text color={theme.heading} wrap="truncate">
            {hints}
          </Text>
        </Box>
        <Box flexShrink={0}>
          <Text dimColor wrap="truncate">
            {`type 5d/20m +⏎  ${gBuffer.length > 0 ? `· ${gBuffer}` : ''}`}
          </Text>
        </Box>
      </Box>
    );
  }
  const selected = view.selected;
  if (selected === null) {
    return (
      <Box flexShrink={0}>
        <Text dimColor>no commit selected</Text>
      </Box>
    );
  }
  // The commit message is pre-WRAPPED by the selector (deterministic line count — no measure-wrap):
  // the sha line, then the wrapped subject + body lines.
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Box flexShrink={0}>
        <Text wrap="truncate">
          <Text color={theme.warning}>{selected.short}</Text>
          <Text dimColor>{' · '}</Text>
          <Text color={theme.heading}>{selected.branch}</Text>
          <Text dimColor>{` · ${selected.age}`}</Text>
        </Text>
      </Box>
      {view.infoLines.map((line, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: wrapped message lines are position-keyed (a commit body can repeat lines; the index IS the stable identity for the fixed-length slice).
        <Box key={`info-${i}`} flexShrink={0}>
          {i === 0 ? (
            <Text color={theme.text} wrap="truncate">
              {line.length > 0 ? line : ' '}
            </Text>
          ) : (
            <Text dimColor wrap="truncate">
              {line.length > 0 ? line : ' '}
            </Text>
          )}
        </Box>
      ))}
    </Box>
  );
}

/** The panel body: loading/error/empty chrome, else the lane blocks + the fixed info section. */
function TransitBody({
  view,
  cursor,
  gPending,
  gBuffer,
}: {
  readonly view: TransitView;
  readonly cursor: TransitCursor;
  readonly gPending: boolean;
  readonly gBuffer: string;
}): React.JSX.Element {
  const theme = useTheme();
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no branches</Text>;
  }
  return (
    <Box flexDirection="column" flexShrink={0}>
      {/* The shared age ruler — one time axis above every lane (a column = the same instant). */}
      <Box flexShrink={0}>
        <Text dimColor wrap="truncate">
          {view.ruler}
        </Text>
      </Box>
      {view.lanes.map((lane, idx) => (
        <LaneRow
          key={lane.branch}
          lane={lane}
          selected={idx === cursor.laneIndex}
          mainIndex={view.mainIndex}
          theme={theme}
        />
      ))}
      <Box flexShrink={0} marginTop={1}>
        <InfoSection view={view} gPending={gPending} gBuffer={gBuffer} theme={theme} />
      </Box>
    </Box>
  );
}

/** The commit on a lane closest in time to `tsEpoch` (for clamping the cursor across lane switches). */
function nearestShaByTime(
  lane: { readonly commits: readonly { readonly sha: string; readonly tsEpoch: number }[] },
  tsEpoch: number | null,
): string | null {
  if (lane.commits.length === 0) {
    return null;
  }
  if (tsEpoch === null) {
    return lane.commits[0]?.sha ?? null;
  }
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

/**
 * The transit panel. Reads the transit slice, runs the selector to display-ready lanes, owns the
 * cursor + the g-capture buffer, declares its keymap, and paints a focus-highlighted Pane.
 * `React.memo`'d (rule 1). The `innerWidth` (R9) is threaded from the budget engine via App's
 * `renderPanel` so the railway scrolls to the width the right rail allots it.
 */
export const TransitPanel = memo(function TransitPanel({
  innerWidth = DEFAULT_INNER_WIDTH,
}: {
  /** The inner width the budget engine grants the railway (R9). Defaults to a nominal width when
   * mounted bare (e.g. a test rendering the panel outside the Rail). */
  readonly innerWidth?: number;
}): React.JSX.Element {
  const transit = useAppStore((s) => s.transit, shallow);
  const refresh = useAppStore((s) => s.actions.transit.refresh);

  // Cursor: which lane + which commit sha is selected. Initialized lazily once lanes load.
  const [cursor, setCursor] = useState<TransitCursor>({ laneIndex: 0, sha: null });
  // The g-capture buffer; `null` = not pending, a string (possibly empty) = pending.
  const [gBuffer, setGBuffer] = useState<string | null>(null);
  const gPending = gBuffer !== null;

  const view = useTransitView(transit, cursor, innerWidth);

  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  // Fetch on first open. The Rail only mounts a panel while it is visible, so this effect runs
  // exactly when the user opens the Git Tree (ctrl+8) — the lazy fetch that replaces the (removed)
  // eager startup prime. It moves the slice off `idle` so the gated invalidation entry in store.ts
  // keeps it live thereafter. `refresh` is a stable store action, so the effect runs once on mount.
  // The body renders `loading…` until the lanes arrive.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Seed the cursor onto the first lane's HEAD once lanes are available and nothing is selected.
  useEffect(() => {
    if (cursor.sha === null && transit.lanes.length > 0) {
      const head = transit.lanes[0]?.headSha ?? null;
      if (head !== null) {
        setCursor({ laneIndex: 0, sha: head });
      }
    }
  }, [cursor.sha, transit.lanes]);

  /** The commit list of the cursor's lane (or null). */
  const selectedLane = transit.lanes[cursor.laneIndex] ?? null;

  /** Move selection older (+1) / newer (-1) within the selected lane, clamped. */
  const moveWithinLane = useCallback(
    (delta: number) => {
      const lane = transit.lanes[cursor.laneIndex];
      if (lane === undefined || lane.commits.length === 0) {
        return;
      }
      const idx = lane.commits.findIndex((c) => c.sha === cursor.sha);
      const current = idx >= 0 ? idx : 0;
      const next = Math.min(Math.max(current + delta, 0), lane.commits.length - 1);
      const sha = lane.commits[next]?.sha ?? null;
      setCursor((c) => ({ ...c, sha }));
    },
    [transit.lanes, cursor.laneIndex, cursor.sha],
  );

  /** Switch lanes (±1), clamping the cursor to the commit nearest in time on the new lane. */
  const switchLane = useCallback(
    (delta: number) => {
      if (transit.lanes.length === 0) {
        return;
      }
      const nextIndex = Math.min(Math.max(cursor.laneIndex + delta, 0), transit.lanes.length - 1);
      if (nextIndex === cursor.laneIndex) {
        return;
      }
      const currentLane = transit.lanes[cursor.laneIndex];
      const currentTs = currentLane?.commits.find((c) => c.sha === cursor.sha)?.tsEpoch ?? null;
      const nextLane = transit.lanes[nextIndex];
      if (nextLane === undefined) {
        return;
      }
      setCursor({ laneIndex: nextIndex, sha: nearestShaByTime(nextLane, currentTs) });
    },
    [transit.lanes, cursor.laneIndex, cursor.sha],
  );

  /** Jump the cursor to a sha; if that sha lives on the main lane, select the main lane (pre-fork). */
  const jumpToSha = useCallback(
    (sha: string) => {
      const mainIndex = transit.lanes.findIndex((l) => l.isMain);
      const mainLane = mainIndex >= 0 ? transit.lanes[mainIndex] : undefined;
      if (mainLane?.commits.some((c) => c.sha === sha)) {
        setCursor({ laneIndex: mainIndex, sha });
        return;
      }
      setCursor((c) => ({ ...c, sha }));
    },
    [transit.lanes],
  );

  /** Handle a printable char while `gPending`: lane-hint jump, duration digit/unit, else ignore. */
  const handleGChar = useCallback(
    (ch: string) => {
      // Lane-hint letter → jump to that lane's HEAD (only if no duration digits typed yet, so a unit
      // letter that doubles as a hint still extends a started buffer).
      const buffer = gBuffer ?? '';
      if (buffer.length === 0) {
        const laneByHint = view.lanes.find((l) => l.hint === ch);
        if (laneByHint !== undefined) {
          const lane = transit.lanes.find((l) => l.branch === laneByHint.branch);
          if (lane !== undefined) {
            const laneIndex = transit.lanes.indexOf(lane);
            setCursor({ laneIndex, sha: lane.headSha });
          }
          setGBuffer(null);
          return;
        }
      }
      if (ch >= '0' && ch <= '9') {
        setGBuffer((b) => (b ?? '') + ch);
        return;
      }
      if (UNIT_LETTERS.has(ch)) {
        setGBuffer((b) => (b ?? '') + ch);
        return;
      }
      // Unrecognized char while pending: ignore (keep the buffer).
    },
    [gBuffer, view.lanes, transit.lanes],
  );

  /** Resolve the duration buffer on `⏎`: parse → resolveDurationJump on the selected lane → jump. */
  const resolveG = useCallback(() => {
    const buffer = gBuffer ?? '';
    const ms = parseDuration(buffer);
    if (ms !== null && selectedLane !== null) {
      const resolved = resolveDurationJump(selectedLane, ms, Date.now());
      if (resolved !== null) {
        jumpToSha(resolved.sha);
      }
    }
    setGBuffer(null);
  }, [gBuffer, selectedLane, jumpToSha]);

  // Build the keymap. Navigation + g-mode control + one chord per a–z / 0–9 char (intent `char:<x>`).
  const keymap: PanelKeymap<TransitIntent> = useMemo(() => {
    const charEntries: KeymapEntry<TransitIntent>[] = [];
    for (const ch of ALPHA + DIGITS) {
      // `hidden`: these are mechanical sub-steps of the `g`-jump gesture (the per-lane label keys),
      // matchable but never hinted — otherwise all 36 a–z/0–9 chords flood the footer. Same treatment
      // as the go-to-line digits (keymap.ts); the live affordance is the single `jump (g)` hint.
      charEntries.push({
        chord: { input: ch },
        intent: `char:${ch}`,
        description: '',
        hidden: true,
      });
    }
    return {
      keymap: [
        // hjkl + arrows. h/l move older/newer along the lane; j/k switch lanes. Declared BEFORE the
        // per-char entries so plain h/j/k/l navigate when NOT in g-mode (the handler routes them to
        // the buffer when pending — they never reach here as chars because these match first).
        {
          chord: [{ input: 'l' }, { key: { rightArrow: true } }],
          intent: 'newer',
          description: 'newer',
        },
        {
          chord: [{ input: 'h' }, { key: { leftArrow: true } }],
          intent: 'older',
          description: 'older',
        },
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'laneDown',
          description: 'next lane',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'laneUp',
          description: 'prev lane',
        },
        { chord: { input: 'g' }, intent: 'startG', description: 'jump (g)' },
        { chord: { key: { return: true } }, intent: 'gEnter', description: 'resolve' },
        { chord: { key: { escape: true } }, intent: 'gEsc', description: 'cancel' },
        ...charEntries,
      ],
      onIntent(intent) {
        // While g-pending, hjkl/g-chars are routed to the capture buffer instead of navigating.
        if (intent.startsWith('char:')) {
          const ch = intent.slice('char:'.length);
          if (gPending) {
            handleGChar(ch);
          }
          return;
        }
        switch (intent) {
          case 'older':
            if (gPending) {
              handleGChar('h');
            } else {
              moveWithinLane(1);
            }
            return;
          case 'newer':
            if (gPending) {
              // 'l' is not a unit/hint by default; ignore in g-mode.
              return;
            }
            moveWithinLane(-1);
            return;
          case 'laneDown':
            if (gPending) {
              return;
            }
            switchLane(1);
            return;
          case 'laneUp':
            if (gPending) {
              return;
            }
            switchLane(-1);
            return;
          case 'startG':
            setGBuffer('');
            return;
          case 'gEnter':
            if (gPending) {
              resolveG();
            }
            return;
          case 'gEsc':
            if (gPending) {
              setGBuffer(null);
            }
            return;
          default:
            // `char:*` intents are handled above (returned early); nothing else remains.
            return;
        }
      },
    };
  }, [gPending, handleGChar, moveWithinLane, switchLane, resolveG]);
  usePanelKeymap(PANEL_ID, keymap);

  return (
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <TransitBody view={view} cursor={cursor} gPending={gPending} gBuffer={gBuffer ?? ''} />
    </Pane>
  );
});
