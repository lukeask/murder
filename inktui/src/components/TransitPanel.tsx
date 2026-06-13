/**
 * TransitPanel — the git commit-graph right-rail panel (panel 8, ctrl+8).
 *
 * Custom {@link ./Pane.tsx Pane} body (like {@link ./UsagePanel.tsx}, NOT a Ledger): per lane it
 * draws TWO deterministic lines — the railway (commit stations + branch tag) and a position-aligned
 * sparse age-marker line — then a FIXED-height info section for the selected commit (sha · branch ·
 * age, subject, wrapped body). All geometry is the selector's (rule 2); this component owns only the
 * cursor + the `g`-jump capture buffer (panel-local `useState`, mirroring the doc-pane pending
 * pattern — it is intentionally NOT a named chord in `bindings.ts`).
 *
 * Row counts are deterministic (the selector builds each line to the known inner width), so this
 * never relies on `measureElement` for wrapped text (the measure-wrap trap). Every lane/info row Box
 * sets `flexShrink={0}` so Yoga never drops a line.
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
  parseDuration,
  resolveDurationJump,
  type TransitCursor,
  type TransitLaneView,
  type TransitView,
  useTransitView,
} from '../selectors/transitSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'transit';
const PANEL_TITLE = 'Transit';

/** The inner width assumed when mounted bare (a test rendering the panel outside the Rail). */
const DEFAULT_INNER_WIDTH = 38;
/** Reserved height (in lines) of the bottom info / hint section: sha line + subject + 2 body lines. */
const INFO_LINES = 4;

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

/** One lane block: the railway line (+ branch tag) then the aligned age-marker line. Both rows are
 * `flexShrink={0}` so Yoga never drops a line; the selected lane's tag is accented. */
function LaneBlock({
  lane,
  selected,
  theme,
}: {
  readonly lane: TransitLaneView;
  readonly selected: boolean;
  readonly theme: ReturnType<typeof useTheme>;
}): React.JSX.Element {
  const tagColor = selected ? theme.focus : lane.isMain ? theme.active : theme.muted;
  const arrow = selected ? '▸' : ' ';
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Box flexShrink={0}>
        <Text wrap="truncate">
          <Text color={selected ? theme.focus : theme.text}>{`${arrow} `}</Text>
          <Text color={theme.success}>{lane.railwayLine}</Text>
          {'  '}
          <Text color={tagColor} bold={selected}>{`▐ ${lane.branchTag} ▌`}</Text>
        </Text>
      </Box>
      <Box flexShrink={0}>
        <Text dimColor wrap="truncate">{`  ${lane.ageLine}`}</Text>
      </Box>
    </Box>
  );
}

/** The fixed-height info section for the selected commit, OR the g-hint overlay when `gPending`. The
 * body is truncated to the lines remaining after the sha + subject lines (deterministic, no measure). */
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
  // Body wrapped/truncated to the lines remaining after the sha line + subject line.
  const bodyLines = Math.max(0, INFO_LINES - 2);
  const body = selected.body.split('\n').slice(0, bodyLines);
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
      <Box flexShrink={0}>
        <Text wrap="truncate">{selected.subject}</Text>
      </Box>
      {body.map((line, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed (commit bodies can repeat lines; the index IS the stable identity for the fixed-length slice).
        <Box key={`body-${i}`} flexShrink={0}>
          <Text dimColor wrap="truncate">
            {line}
          </Text>
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
      {view.lanes.map((lane, idx) => (
        <LaneBlock
          key={lane.branch}
          lane={lane}
          selected={idx === cursor.laneIndex}
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

  // Refresh on mount/focus, mirroring history's pull-on-mount.
  useEffect(() => {
    if (focused) {
      void refresh();
    }
  }, [focused, refresh]);

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
      charEntries.push({ chord: { input: ch }, intent: `char:${ch}`, description: '' });
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
