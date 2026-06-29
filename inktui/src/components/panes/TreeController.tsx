import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { usePanelKeymap } from '../../hooks/useInputStores.js';
import type { KeymapEntry, PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import {
  parseDuration,
  resolveDurationJump,
  type TransitCursor,
  type TransitView,
  useTransitView,
} from '../../selectors/transitSelectors.js';
import type { Theme } from '../../theme/buildTheme.js';
import { useTheme } from '../../theme/themeStore.js';
import { paneContentWidthForWidth } from '../Pane.js';
import { MeasuredPaneFrame } from './shared/MeasuredPaneFrame.js';
import { TreeSurface, type TreeSurfaceData, type TreeSurfaceLane } from './TreeSurface.js';

type TreeIntent =
  | 'older'
  | 'newer'
  | 'laneDown'
  | 'laneUp'
  | 'startG'
  | 'gEnter'
  | 'gEsc'
  | `char:${string}`;

const TREE_ALPHA = 'abcdefghijklmnopqrstuvwxyz';
const TREE_DIGITS = '0123456789';
const TREE_UNIT_LETTERS = new Set(['m', 'h', 'd', 'w']);

function treeLaneColor(index: number, mainIndex: number, theme: Theme): string {
  if (index === mainIndex) {
    return theme.active;
  }
  return theme.laneColors[index % theme.laneColors.length] ?? theme.text;
}

function transitRailText(lane: TransitView['lanes'][number]): string {
  return lane.segments.map((segment) => segment.text).join('');
}

export function treeSurfaceDataFromView(
  view: TransitView,
  cursor: TransitCursor,
  gPending: boolean,
  gBuffer: string,
  theme: Theme,
): TreeSurfaceData {
  const lanes: TreeSurfaceLane[] = view.lanes.map((lane, index) => ({
    branch: lane.branch,
    rail: transitRailText(lane),
    color: treeLaneColor(lane.colorIndex, view.mainIndex, theme),
    selected: index === cursor.laneIndex,
  }));
  const info = gPending
    ? [
        view.lanes.map((lane) => `[${lane.hint}] ${lane.branch}`).join('  '),
        `type 5d/20m +⏎  ${gBuffer.length > 0 ? `· ${gBuffer}` : ''}`,
      ]
    : view.selected === null
      ? ['no commit selected']
      : [
          `${view.selected.short} · ${view.selected.branch} · ${view.selected.age}`,
          ...view.infoLines,
        ];
  return {
    ruler: view.ruler,
    lanes,
    info,
    pending: gPending,
    status: view.status,
    error: view.error,
  };
}

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

export interface TreeControllerProps {
  readonly presentation: PanePresentation;
}

export const TreeController = memo(function TreeController({
  presentation,
}: TreeControllerProps): React.JSX.Element {
  const transit = useAppStore((state) => state.transit, shallow);
  const refresh = useAppStore((state) => state.actions.transit.refresh);
  const theme = useTheme();
  const innerWidth = paneContentWidthForWidth(presentation.width);
  const [cursor, setCursor] = useState<TransitCursor>({ laneIndex: 0, sha: null });
  const [gBuffer, setGBuffer] = useState<string | null>(null);
  const gPending = gBuffer !== null;
  const view = useTransitView(transit, cursor, innerWidth);
  const data = useMemo(
    () => treeSurfaceDataFromView(view, cursor, gPending, gBuffer ?? '', theme),
    [cursor, gBuffer, gPending, theme, view],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (cursor.sha === null && transit.lanes.length > 0) {
      const head = transit.lanes[0]?.headSha ?? null;
      if (head !== null) {
        setCursor({ laneIndex: 0, sha: head });
      }
    }
  }, [cursor.sha, transit.lanes]);

  const selectedLane = transit.lanes[cursor.laneIndex] ?? null;

  const moveWithinLane = useCallback(
    (delta: number) => {
      const lane = transit.lanes[cursor.laneIndex];
      if (lane === undefined || lane.commits.length === 0) {
        return;
      }
      const index = lane.commits.findIndex((commit) => commit.sha === cursor.sha);
      const current = index >= 0 ? index : 0;
      const next = Math.min(Math.max(current + delta, 0), lane.commits.length - 1);
      const sha = lane.commits[next]?.sha ?? null;
      setCursor((currentCursor) => ({ ...currentCursor, sha }));
    },
    [cursor.laneIndex, cursor.sha, transit.lanes],
  );

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
      const currentTs =
        currentLane?.commits.find((commit) => commit.sha === cursor.sha)?.tsEpoch ?? null;
      const nextLane = transit.lanes[nextIndex];
      if (nextLane === undefined) {
        return;
      }
      setCursor({ laneIndex: nextIndex, sha: nearestShaByTime(nextLane, currentTs) });
    },
    [cursor.laneIndex, cursor.sha, transit.lanes],
  );

  const jumpToSha = useCallback(
    (sha: string) => {
      const mainIndex = transit.lanes.findIndex((lane) => lane.isMain);
      const mainLane = mainIndex >= 0 ? transit.lanes[mainIndex] : undefined;
      if (mainLane?.commits.some((commit) => commit.sha === sha)) {
        setCursor({ laneIndex: mainIndex, sha });
        return;
      }
      setCursor((current) => ({ ...current, sha }));
    },
    [transit.lanes],
  );

  const handleGChar = useCallback(
    (ch: string) => {
      const buffer = gBuffer ?? '';
      if (buffer.length === 0) {
        const laneByHint = view.lanes.find((lane) => lane.hint === ch);
        if (laneByHint !== undefined) {
          const lane = transit.lanes.find((candidate) => candidate.branch === laneByHint.branch);
          if (lane !== undefined) {
            const laneIndex = transit.lanes.indexOf(lane);
            setCursor({ laneIndex, sha: lane.headSha });
          }
          setGBuffer(null);
          return;
        }
      }
      if (ch >= '0' && ch <= '9') {
        setGBuffer((current) => (current ?? '') + ch);
        return;
      }
      if (TREE_UNIT_LETTERS.has(ch)) {
        setGBuffer((current) => (current ?? '') + ch);
      }
    },
    [gBuffer, transit.lanes, view.lanes],
  );

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
  }, [gBuffer, jumpToSha, selectedLane]);

  const keymap: PanelKeymap<TreeIntent> = useMemo(() => {
    const charEntries: KeymapEntry<TreeIntent>[] = [];
    for (const ch of TREE_ALPHA + TREE_DIGITS) {
      charEntries.push({
        chord: { input: ch },
        intent: `char:${ch}`,
        description: '',
        hidden: true,
      });
    }
    return {
      keymap: [
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
        if (intent.startsWith('char:')) {
          if (gPending) {
            handleGChar(intent.slice('char:'.length));
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
            if (!gPending) {
              moveWithinLane(-1);
            }
            return;
          case 'laneDown':
            if (!gPending) {
              switchLane(1);
            }
            return;
          case 'laneUp':
            if (!gPending) {
              switchLane(-1);
            }
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
            return;
        }
      },
    };
  }, [gPending, handleGChar, moveWithinLane, resolveG, switchLane]);
  usePanelKeymap('tree', keymap);

  return (
    <MeasuredPaneFrame id="tree" presentation={presentation}>
      <TreeSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        data={data}
      />
    </MeasuredPaneFrame>
  );
});
