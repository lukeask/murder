/**
 * BottomBar — contextual hints (the plan's "Bottom bar: contextual hints"). Shows the global chords
 * always, plus the *focused* panel's declared keys, sourced straight from its keymap so a declared
 * key is self-documenting (see keymap.ts). A pure function of the effective focus + the focused
 * panel's registered keymap; the formatting lives in {@link selectBottomBar} (rule 2).
 */

import { Box, Text } from 'ink';
import { memo, useEffect, useMemo, useState } from 'react';
import { useStore } from 'zustand';
import {
  useBindings,
  useEffectiveFocus,
  useInputStores,
  useKeymapRegistry,
  useModeStore,
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
import { type BottomBarHint, selectBottomBar } from '../selectors/barSelectors.js';
import {
  MAX_VISIBLE_TOASTS,
  selectLiveToasts,
  TOAST_EXIT_MS,
  type Toast as ToastData,
  type ToastSeverity,
  toastStore,
} from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';

/** Horizontal gap (cells) between hints on a line — matches the rendered `columnGap`. Single cell:
 * the hints already carry their own key/description spacing, so a 1-cell gap reads as distinct chips
 * while packing more onto each line (the user's tighter-footer ask). */
const HINT_GAP = 1;
/** `paddingX={1}` each side of the bar. */
const BAR_PADDING = 2;
const TOAST_ENTER_MS = 400;
const TOAST_TICK_MS = 50;
const TOAST_GAP = 1;
const TOAST_RIGHT_PAD = 1;

/** Display width of one hint: `key` + a space + `description`. An empty description (e.g. the
 * chat-focus `:help` hint, which is self-describing) drops the trailing word but keeps the chip. */
function hintWidth(hint: BottomBarHint): number {
  return hint.description.length === 0
    ? hint.key.length
    : hint.key.length + 1 + hint.description.length;
}

/** Rendered width of a whole line: each hint's width plus one {@link HINT_GAP} between adjacent ones. */
function lineWidth(line: readonly BottomBarHint[]): number {
  return (
    line.reduce((sum, hint) => sum + hintWidth(hint), 0) + HINT_GAP * Math.max(0, line.length - 1)
  );
}

/**
 * Greedily pack hints into lines that each fit `avail` cells (left-to-right, `HINT_GAP` between
 * hints). Returns one array per rendered line. We pack in JS — rather than lean on `flexWrap` — so
 * the bar renders as N EXPLICIT single-line rows. This is also the row count the Shell needs for the
 * Body height (see {@link useBottomBarLines}): Ink does NOT compute the wrapped height of an
 * overflowing flex row OR of a percentage-width wrapping Text (both measure as 1 line while the
 * terminal draws 2), so `measureElement` on the footer is unreliable. Computing the line count
 * deterministically makes the Shell's portrait Body-height math exact.
 *
 * Right-aligned hints (`align: 'right'`, item 12 prep) are pulled out of the left-to-right flow and
 * appended to the LAST line; the renderer pins them to the far edge via `justifyContent="space-between"`.
 * A line that carries a right-aligned hint is detectable by `line.some((h) => h.align === 'right')`.
 */
export function packHints(hints: readonly BottomBarHint[], avail: number): BottomBarHint[][] {
  const right = hints.filter((h) => h.align === 'right');
  const left = hints.filter((h) => h.align !== 'right');
  const lines: BottomBarHint[][] = [];
  let current: BottomBarHint[] = [];
  let used = 0;
  for (const hint of left) {
    const w = hintWidth(hint);
    const add = current.length === 0 ? w : w + HINT_GAP;
    if (current.length > 0 && used + add > avail) {
      lines.push(current);
      current = [hint];
      used = w;
    } else {
      current.push(hint);
      used += add;
    }
  }
  if (current.length > 0) {
    lines.push(current);
  }
  if (right.length > 0) {
    // Pin the right-aligned hints to the last line's far edge (a fresh line if there are no left
    // hints), so the bar's row count stays minimal and the help hint always sits bottom-right. But
    // only if the right cluster actually fits after the last line's left flow (plus a gap); otherwise
    // it would collide with the left hints under `space-between`, so drop it onto its own line for a
    // cleaner stack on a narrow terminal.
    const last = lines[lines.length - 1];
    if (last !== undefined && lineWidth(last) + HINT_GAP + lineWidth(right) <= avail) {
      last.push(...right);
    } else {
      lines.push([...right]);
    }
  }
  return lines;
}

/**
 * The footer's hints, packed into rendered lines for the live terminal width. Shared by the
 * {@link BottomBar} (which renders the lines) and the Shell (which needs `lines.length` to compute
 * the portrait Body height — App.tsx). One source of truth so the rendered row count and the height
 * accounting can never disagree.
 */
export function useBottomBarLines(): BottomBarHint[][] {
  const focused = useEffectiveFocus();
  // The focused panel's declared keymap (undefined when chat is focused — chat has no panel keymap).
  const focusedKeymap = useKeymapRegistry((s) =>
    focused === CHAT_FOCUS ? undefined : s.keymaps[focused]?.keymap,
  );
  const bindings = useBindings();
  // An active mode (the spawn wizard, help overlay, …) can own the bar with its own hints; subscribe
  // to the stack so a push/pop re-derives, and read the active mode's hints through the same selector
  // the Overlay uses. `undefined` (no mode, or a mode without hints) falls back to the panel keys.
  const { modes } = useInputStores();
  useModeStore((s) => s.stack);
  const modeHints = selectActiveMode(modes)?.hints;
  const hints = useMemo(
    () => selectBottomBar(focused, focusedKeymap, bindings, modeHints),
    [focused, focusedKeymap, bindings, modeHints],
  );
  const { columns } = useTerminalSize();
  return useMemo(() => packHints(hints, Math.max(1, columns - BAR_PADDING)), [hints, columns]);
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function easeOut(value: number): number {
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
  return x + hintWidth(hint);
}

function renderHintLine(
  line: readonly BottomBarHint[],
  width: number,
  theme: ReturnType<typeof useTheme>,
  overlays: readonly CellOverlay[],
): TextRun[] {
  const surface = createSurface(width, 1);
  const left = line.filter((h) => h.align !== 'right');
  const right = line.filter((h) => h.align === 'right');
  let x = 0;
  for (const hint of left) {
    x = putHint(surface, x, hint, theme) + HINT_GAP;
  }
  if (right.length > 0) {
    let rightX = Math.max(0, width - lineWidth(right));
    for (const hint of right) {
      rightX = putHint(surface, rightX, hint, theme) + HINT_GAP;
    }
  }
  applyOverlays(surface, overlays);
  return renderSurface(surface);
}

function SurfaceText({ runs }: { readonly runs: readonly TextRun[] }): React.JSX.Element {
  const occurrences = new Map<string, number>();
  const keyedRuns = runs.map((run) => {
    const identity = JSON.stringify([
      run.text,
      run.style.fg,
      run.style.bg,
      run.style.bold,
      run.style.dim,
    ]);
    const occurrence = occurrences.get(identity) ?? 0;
    occurrences.set(identity, occurrence + 1);
    return { key: `${identity}:${occurrence}`, run };
  });
  return (
    <Text>
      {keyedRuns.map(({ key, run }) => {
        const props = {
          ...(run.style.fg !== undefined ? { color: run.style.fg } : {}),
          ...(run.style.bg !== undefined ? { backgroundColor: run.style.bg } : {}),
          ...(run.style.bold !== undefined ? { bold: run.style.bold } : {}),
          ...(run.style.dim !== undefined ? { dimColor: run.style.dim } : {}),
        };
        return (
          <Text key={key} {...props}>
            {run.text}
          </Text>
        );
      })}
    </Text>
  );
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
        const key = line.map((h) => h.key).join('|');
        const overlays =
          index === lines.length - 1
            ? buildToastOverlays({ width, toasts: liveToasts, now, theme })
            : [];
        const runs = renderHintLine(line, width, theme, overlays);
        return (
          <Box key={key} flexDirection="row">
            <SurfaceText runs={runs} />
          </Box>
        );
      })}
    </Box>
  );
});
