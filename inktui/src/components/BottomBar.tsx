/**
 * BottomBar — contextual hints (the plan's "Bottom bar: contextual hints"). Shows the global chords
 * always, plus the *focused* panel's declared keys, sourced straight from its keymap so a declared
 * key is self-documenting (see keymap.ts). A pure function of the effective focus + the focused
 * panel's registered keymap; the formatting lives in {@link selectBottomBar} (rule 2).
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { useEffectiveFocus, useKeymapRegistry } from '../hooks/useInputStores.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import { CHAT_FOCUS } from '../input/focusStore.js';
import { type BottomBarHint, selectBottomBar } from '../selectors/barSelectors.js';
import { theme } from '../theme.js';

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
 */
export function packHints(hints: readonly BottomBarHint[], avail: number): BottomBarHint[][] {
  const lines: BottomBarHint[][] = [];
  let current: BottomBarHint[] = [];
  let used = 0;
  for (const hint of hints) {
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
  const hints = useMemo(() => selectBottomBar(focused, focusedKeymap), [focused, focusedKeymap]);
  const { columns } = useTerminalSize();
  return useMemo(() => packHints(hints, Math.max(1, columns - BAR_PADDING)), [hints, columns]);
}

export const BottomBar = memo(function BottomBar(): React.JSX.Element {
  const lines = useBottomBarLines();
  return (
    <Box flexDirection="column" width="100%" paddingX={1}>
      {lines.map((line) => (
        <Box key={line.map((h) => h.key).join('|')} flexDirection="row" columnGap={HINT_GAP}>
          {line.map((hint) => (
            <Text key={`${hint.key}:${hint.description}`} dimColor>
              <Text color={theme.warning}>{hint.key}</Text> {hint.description}
            </Text>
          ))}
        </Box>
      ))}
    </Box>
  );
});
