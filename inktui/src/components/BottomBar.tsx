/**
 * BottomBar — contextual hints (the plan's "Bottom bar: contextual hints"). Shows the global chords
 * always, plus the *focused* panel's declared keys, sourced straight from its keymap so a declared
 * key is self-documenting (see keymap.ts). A pure function of the effective focus + the focused
 * panel's registered keymap; the formatting lives in {@link selectBottomBar} (rule 2).
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
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
import { type BottomBarHint, selectBottomBar } from '../selectors/barSelectors.js';
import { useTheme } from '../theme/themeStore.js';

/** Horizontal gap (cells) between hints on a line — matches the rendered `columnGap`. */
const HINT_GAP = 2;
/** `paddingX={1}` each side of the bar. */
const BAR_PADDING = 2;

/** Display width of one hint: `key` + a space + `description`. */
function hintWidth(hint: BottomBarHint): number {
  return hint.key.length + 1 + hint.description.length;
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
    // hints), so the bar's row count stays minimal and the help hint always sits bottom-right.
    if (lines.length === 0) {
      lines.push([...right]);
    } else {
      const last = lines[lines.length - 1] as BottomBarHint[];
      last.push(...right);
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

/** One hint chip — `key` (accented) then its description. */
function HintChip({ hint }: { readonly hint: BottomBarHint }): React.JSX.Element {
  const theme = useTheme();
  return (
    <Text dimColor>
      <Text color={theme.warning}>{hint.key}</Text> {hint.description}
    </Text>
  );
}

export const BottomBar = memo(function BottomBar(): React.JSX.Element {
  const lines = useBottomBarLines();
  return (
    <Box flexDirection="column" width="100%" paddingX={1}>
      {lines.map((line) => {
        // Right-aligned hints (item 12 prep) are pinned to the far edge: split the line into its
        // left flow and its right cluster, and let `space-between` push them apart. A line with no
        // right hints renders as a plain left-to-right row.
        const left = line.filter((h) => h.align !== 'right');
        const right = line.filter((h) => h.align === 'right');
        const key = line.map((h) => h.key).join('|');
        if (right.length === 0) {
          return (
            <Box key={key} flexDirection="row" columnGap={HINT_GAP}>
              {left.map((hint) => (
                <HintChip key={`${hint.key}:${hint.description}`} hint={hint} />
              ))}
            </Box>
          );
        }
        return (
          <Box key={key} flexDirection="row" justifyContent="space-between">
            <Box flexDirection="row" columnGap={HINT_GAP}>
              {left.map((hint) => (
                <HintChip key={`${hint.key}:${hint.description}`} hint={hint} />
              ))}
            </Box>
            <Box flexDirection="row" columnGap={HINT_GAP}>
              {right.map((hint) => (
                <HintChip key={`${hint.key}:${hint.description}`} hint={hint} />
              ))}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
});
