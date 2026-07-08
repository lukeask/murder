/**
 * BottomBar — contextual hints (the plan's "Bottom bar: contextual hints"). Shows the global chords
 * always, plus the *focused* panel's declared keys, sourced straight from its keymap so a declared
 * key is self-documenting (see keymap.ts). A pure function of the effective focus + the focused
 * panel's registered keymap; the formatting lives in {@link selectBottomBarLineItems} (rule 2).
 *
 * Phase 3.1: hints are a toggleable built-in bar widget; other bottom widgets pack onto lines via
 * {@link packBottomBarLineItems} (never Ink flexWrap).
 */

import { Box } from 'ink';
import { memo, useEffect, useMemo, useState } from 'react';
import { useStore } from 'zustand';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
  useEffectiveFocus,
  useInputStores,
  useKeymapRegistry,
  useModeStore,
  useWorkspaceStore,
} from '../hooks/useInputStores.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import { CHAT_FOCUS } from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import {
  applyOverlays,
  type CellOverlay,
  type CellStyle,
  cellsFromText,
  createSurface,
  putText,
  renderSurface,
  type TextRun,
} from '../render/cellSurface.js';
import {
  BOTTOM_BAR_ITEM_GAP,
  type BottomBarHint,
  type BottomBarLineItem,
  bottomBarItemWidth,
  bottomBarLineWidth,
  packBottomBarLineItems,
  selectBottomBarLineItems,
} from '../selectors/barSelectors.js';
import { keyUsageStore } from '../store/keyUsage/keyUsageStore.js';
import {
  MAX_VISIBLE_TOASTS,
  selectLiveToasts,
  TOAST_EXIT_MS,
  type Toast as ToastData,
  type ToastSeverity,
  toastStore,
} from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { TextRuns } from './TextRuns.js';

/** `paddingX={1}` each side of the bar. */
const BAR_PADDING = 2;
const TOAST_ENTER_MS = 400;
const TOAST_TICK_MS = 50;
const TOAST_GAP = 1;
const TOAST_RIGHT_PAD = 1;

/**
 * The footer's lines, packed for the live terminal width. Shared by {@link BottomBar} and the Shell
 * (Body height accounting in App.tsx). When packing yields zero lines (hints widget disabled) but
 * toasts are held, a single blank line is included so the toast overlays have a host row — decided
 * here so the render and the Shell's height budget can never disagree. The store self-prunes each
 * toast at ttl + exit grace, so the `toasts.length` subscription tracks the host line's lifetime.
 */
export function useBottomBarLines(): BottomBarLineItem[][] {
  const focused = useEffectiveFocus();
  const focusedKeymap = useKeymapRegistry((s) =>
    focused === CHAT_FOCUS ? undefined : s.keymaps[focused]?.keymap,
  );
  const bindings = useBindings();
  const barWidgets = useAppStore((s) => s.settings.barWidgets);
  const usage = useAppStore((s) => s.usage);
  const activeIndex = useWorkspaceStore((s) => s.activeIndex);
  const count = useWorkspaceStore((s) => s.count);
  const keyUsage = useStore(keyUsageStore, (s) => s.actions);
  const { modes } = useInputStores();
  useModeStore((s) => s.stack);
  const modeHints = selectActiveMode(modes)?.hints;
  const { columns } = useTerminalSize();
  const avail = Math.max(1, columns - BAR_PADDING);
  const now = Date.now();
  const items = useMemo(
    () =>
      selectBottomBarLineItems(
        barWidgets,
        focused,
        focusedKeymap,
        bindings,
        { usage, keyUsage, now, activeIndex, count },
        avail,
        modeHints,
      ),
    [
      barWidgets,
      focused,
      focusedKeymap,
      bindings,
      modeHints,
      usage,
      keyUsage,
      avail,
      now,
      activeIndex,
      count,
    ],
  );
  const hasToasts = useStore(toastStore, (s) => s.toasts.length > 0);
  return useMemo(() => {
    const lines = packBottomBarLineItems(items, avail);
    return lines.length === 0 && hasToasts ? [[]] : lines;
  }, [items, avail, hasToasts]);
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Cubic ease-out over a clamped 0–1 input. Shared with the toast enter/exit widths here and the
 * workspace slide offset ({@link ./WorkspaceSlideOverlay.js}). */
export function easeOut(value: number): number {
  const t = clamp01(value);
  return 1 - (1 - t) ** 3;
}

function toastLabel(toast: ToastData): string {
  return toast.count > 1 ? `${toast.text} (x${toast.count})` : toast.text;
}

function toastSeverityStyle(
  severity: ToastSeverity,
  theme: ReturnType<typeof useTheme>,
): CellStyle {
  if (severity === 'error') {
    return { fg: theme.error, bg: theme.panelSelectedBg, bold: true };
  }
  if (severity === 'warning') {
    return { fg: theme.warning, bg: theme.panelSelectedBg };
  }
  return { fg: theme.muted, bg: theme.panelSelectedBg };
}

function toastEffectiveWidth(toast: ToastData, fullWidth: number, now: number): number {
  const enterDoneAt = toast.createdAt + TOAST_ENTER_MS;
  if (now < enterDoneAt) {
    return Math.round(fullWidth * easeOut((now - toast.createdAt) / TOAST_ENTER_MS));
  }
  if (now <= toast.expiresAt) {
    return fullWidth;
  }
  return Math.round(fullWidth * (1 - easeOut((now - toast.expiresAt) / TOAST_EXIT_MS)));
}

function buildToastOverlays({
  width,
  toasts,
  now,
  theme,
}: {
  readonly width: number;
  readonly toasts: readonly ToastData[];
  readonly now: number;
  readonly theme: ReturnType<typeof useTheme>;
}): CellOverlay[] {
  const items = toasts
    .slice(-MAX_VISIBLE_TOASTS)
    .map((toast) => {
      const cells = cellsFromText(
        ` ${toastLabel(toast)} `,
        toastSeverityStyle(toast.severity, theme),
      );
      return { toast, cells, effectiveWidth: toastEffectiveWidth(toast, cells.length, now) };
    })
    .filter((item) => item.effectiveWidth > 0)
    .sort((a, b) => a.toast.createdAt - b.toast.createdAt || a.toast.id - b.toast.id);

  const overlays: CellOverlay[] = [];
  let xRight = width - TOAST_RIGHT_PAD - 1;
  for (const item of items.toReversed()) {
    const effectiveWidth = Math.min(item.effectiveWidth, item.cells.length);
    const x = xRight - effectiveWidth + 1;
    overlays.push({
      x,
      y: 0,
      cells: item.cells.slice(item.cells.length - effectiveWidth),
    });
    xRight = x - 1 - TOAST_GAP;
  }
  return overlays;
}

function putHint(
  surface: ReturnType<typeof createSurface>,
  x: number,
  hint: BottomBarHint,
  theme: ReturnType<typeof useTheme>,
): number {
  if (hint.description.length === 0) {
    putText(surface, x, 0, hint.key, { fg: theme.warning });
    return x + hint.key.length;
  }
  putText(surface, x, 0, hint.key, { fg: theme.warning, dim: true });
  putText(surface, x + hint.key.length, 0, ` ${hint.description}`, { dim: true });
  return x + bottomBarItemWidth({ kind: 'hint', hint });
}

function putSegment(
  surface: ReturnType<typeof createSurface>,
  x: number,
  runs: readonly TextRun[],
): number {
  let cursor = x;
  for (const run of runs) {
    putText(surface, cursor, 0, run.text, run.style);
    cursor += run.text.length;
  }
  return cursor;
}

function renderBarLine(
  line: readonly BottomBarLineItem[],
  width: number,
  theme: ReturnType<typeof useTheme>,
  overlays: readonly CellOverlay[],
): TextRun[] {
  const surface = createSurface(width, 1);
  const left = line.filter((item) => !(item.kind === 'hint' && item.hint.align === 'right'));
  const right = line.filter((item) => item.kind === 'hint' && item.hint.align === 'right');
  let x = 0;
  for (const item of left) {
    if (item.kind === 'hint') {
      x = putHint(surface, x, item.hint, theme) + BOTTOM_BAR_ITEM_GAP;
    } else {
      x = putSegment(surface, x, item.runs) + BOTTOM_BAR_ITEM_GAP;
    }
  }
  if (right.length > 0) {
    let rightX = Math.max(0, width - bottomBarLineWidth(right));
    for (const item of right) {
      if (item.kind === 'hint') {
        rightX = putHint(surface, rightX, item.hint, theme) + BOTTOM_BAR_ITEM_GAP;
      } else {
        rightX = putSegment(surface, rightX, item.runs) + BOTTOM_BAR_ITEM_GAP;
      }
    }
  }
  applyOverlays(surface, overlays);
  return renderSurface(surface);
}

function lineKey(line: readonly BottomBarLineItem[]): string {
  return line
    .map((item) =>
      item.kind === 'hint'
        ? `${item.hint.key}:${item.hint.description}`
        : `segment:${item.widgetId}`,
    )
    .join('|');
}

export const BottomBar = memo(function BottomBar(): React.JSX.Element {
  const lines = useBottomBarLines();
  const { columns } = useTerminalSize();
  const width = Math.max(1, columns - BAR_PADDING);
  const theme = useTheme();
  const toasts = useStore(toastStore, (s) => s.toasts);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (toasts.length === 0) {
      return;
    }
    const handle = setInterval(() => setNow(Date.now()), TOAST_TICK_MS);
    return () => clearInterval(handle);
  }, [toasts.length]);

  const liveToasts = selectLiveToasts(toasts, now);
  return (
    <Box flexDirection="column" width="100%" paddingX={1}>
      {lines.map((line, index) => {
        const overlays =
          index === lines.length - 1
            ? buildToastOverlays({ width, toasts: liveToasts, now, theme })
            : [];
        const runs = renderBarLine(line, width, theme, overlays);
        return (
          <Box key={lineKey(line)} flexDirection="row">
            <TextRuns runs={runs} />
          </Box>
        );
      })}
    </Box>
  );
});
