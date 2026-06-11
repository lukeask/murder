/**
 * paneBorder — the shared inline-title top-border row used by {@link ./Pane.tsx Pane} and
 * {@link ./ChatInput.tsx ChatInput}.
 *
 * Both want the `╭─ Title ──────────────╮` look: a hand-composed flex row drawn ON the top border
 * line, with the other three sides supplied by Ink's own `borderStyle="round"` + `borderTop={false}`
 * on the content box below. The recipe was first developed in `Pane` (see its header for the Yoga
 * quirks it relies on); rather than duplicate that JSX in ChatInput (spec nit: the chat input gets
 * the same border), the row is extracted here and used by both.
 *
 * Layout (one terminal line, `height={1}` so the `─` fill never wraps vertically):
 *
 *   ╭─  <title>   ───────────  ╮
 *   └fixed┘ └fixed┘ └fill────┘ └fixed┘
 *
 * The corner segments are `flexShrink={0}` so the corners are always drawn; the fill is a long
 * `─`-run in a `flexGrow={1} flexBasis={0} overflow="hidden"` box (`wrap="hard"`), so flexbox sizes
 * it to the leftover width and the box clips the overrun — no measured width, no setState, no flicker.
 *
 * ## Title-overflow guarantee (L3b — correctness)
 * The `╭─ ` / leading-space / `╮` corner segments stay `flexShrink={0}` so the corners are ALWAYS
 * drawn — the border `╮` closes at any rail width. The TITLE segment is `flexShrink={1}` +
 * `minWidth={0}` + `overflow="hidden"`: when a panel is narrower than `╭─ ` + title + titleExtra +
 * ` ` + `╮`, the title segment shrinks and CLIPS instead of pushing the `╮` off the edge (the bug:
 * `╭─ Crows [mini]` with no closing `╮`). The clip lives on the title-segment BOX (not just
 * `wrap="truncate-end"` on the title text) because the overflow is usually the `titleExtra` SIBLING
 * — a short title ("Crows") with a long suffix ("[max]") — and text-wrap on the title alone can't
 * shrink a separate node. This is the f26b77a clipping discipline applied to the title row.
 *
 * The catch a shrinkable title introduces (and how `flexBasis={0}` on the fill resolves it): with
 * the default `auto` basis the fill's 256-char run makes the title row ALWAYS overflow 100%, so a
 * shrinkable title would be elided even on a WIDE pane (Yoga drags the perpetual overflow onto every
 * shrinkable sibling). `flexBasis={0}` zeroes the fill's natural width, so on a normal pane there is
 * POSITIVE free space (the fill GROWS into it) and the title is left full; only on a genuinely too-
 * narrow pane is there negative space, which the fill (shrink weight `1 × 0 = 0`) absorbs none of —
 * so the title alone clips. For ChatInput (title `›`, no extra) the change is inert: the title is one
 * char, so there is always slack and it never shrinks.
 *
 * Presentational only (rule 1): a pure function of its colors + title; no store/selector/bus access,
 * no `useInput` (rule 5). Colors arrive resolved (see {@link ./Pane.tsx paneColors}).
 */

import { Box, Text } from 'ink';
import { TRI_DOWN, TRI_UP } from './glyphs.js';

/**
 * The scroll-overflow indicator drawn on the border tail: ` ─ ▴ N ──` (top) / ` ─ ▾ N ──` (bottom).
 * ONE `flexShrink={0}` Box so it NEVER shrinks — on a too-narrow rail the title/fill clip but the
 * count stays legible. The triangle + dashes are `borderColor`, the count is `dimColor`. Slot it
 * BETWEEN the `─`-fill and the closing corner so it reads as `…──── ▴ N ──╮`.
 */
function OverflowIndicator({
  tri,
  count,
  borderColor,
}: {
  readonly tri: string;
  readonly count: number;
  readonly borderColor: string;
}): React.JSX.Element {
  return (
    <Box flexShrink={0}>
      <Text color={borderColor}>{`─ ${tri} `}</Text>
      <Text color={borderColor} dimColor>
        {String(count)}
      </Text>
      <Text color={borderColor}>{' ──'}</Text>
    </Box>
  );
}

export interface PaneBorderTopProps {
  /** Display-ready title text shown inline on the border (e.g. `Plans`, or `›` for the chat input). */
  readonly title: string;
  /** Border + corner + `─`-fill color — a {@link ../theme.js theme} role resolved by `paneColors`. */
  readonly borderColor: string;
  /** Title-segment color — a {@link ../theme.js theme} role (see {@link ./Pane.tsx paneColors}). */
  readonly titleColor: string;
  /** True → render the title bold (matches the focused emphasis the old panels used). */
  readonly bold?: boolean;
  /** Optional trailing node placed right after the title text, inside the title segment. The CALLER
   * owns its color (pass a styled node) — see Pane's `titleExtra` handoff note. */
  readonly titleExtra?: React.ReactNode;
  /** Rows hidden ABOVE the viewport. When `> 0`, a fixed `─ ▴ N ──` indicator is drawn on the border
   * tail (right of the `─`-fill, before `╮`); absent/0 renders nothing extra — byte-identical to today. */
  readonly overflowAbove?: number | undefined;
  /** Rows hidden BELOW the viewport. When `> 0`, a fixed `─ ▾ N ──` indicator is drawn on the border
   * tail (right of any `▴ N`, before `╮`). It lives on the TOP border — NOT a separate bottom row —
   * because a fixed-height bottom row clips off at fractional pane heights (the split `[top, flexGrow,
   * bottom]` column loses its trailing fixed cell when the pane's height rounds to a half-cell, while
   * Ink's own border, drawn ON the content box, never does). So the content box draws the robust Ink
   * bottom border and BOTH scroll indicators ride the top border. Absent/0 renders nothing extra. */
  readonly overflowBelow?: number | undefined;
}

/**
 * The inline-title top-border row. `height={1}` keeps the `─` fill on a single line (otherwise
 * `wrap="hard"` would wrap the 256-char run vertically). The fill (`flexGrow={1} flexBasis={0}` +
 * `overflow="hidden"`) grows into the slack and clips cleanly; the CORNER segments never shrink so
 * the `╮` always closes, while the title segment shrinks + clips on a too-narrow rail (L3b — see the
 * header note for why the fill's `flexBasis={0}` is what keeps a wide title intact).
 */
export function PaneBorderTop({
  title,
  borderColor,
  titleColor,
  bold = false,
  titleExtra,
  overflowAbove,
  overflowBelow,
}: PaneBorderTopProps): React.JSX.Element {
  return (
    <Box flexDirection="row" flexShrink={0} width="100%" height={1}>
      <Box flexShrink={0}>
        <Text color={borderColor}>{'╭─ '}</Text>
      </Box>
      {/* Title segment: SHRINKABLE + clipped so a title/suffix wider than the rail truncates rather
          than pushing the `╮` corner off the edge (L3b). The fixed corner segments above/below never
          shrink, so the border always closes. */}
      <Box flexShrink={1} minWidth={0} overflow="hidden">
        <Text color={titleColor} bold={bold} wrap="truncate-end">
          {title}
        </Text>
        {titleExtra}
      </Box>
      <Box flexShrink={0}>
        <Text color={borderColor}> </Text>
      </Box>
      {/* The `─` fill: `flexBasis={0}` is load-bearing (L3b). With the default `auto` basis the
          256-char run is the box's natural width, so the title row ALWAYS overflows 100% — and once
          the title segment is `flexShrink={1}`, Yoga drags that perpetual overflow onto it, eliding
          the title even on a WIDE pane. `flexBasis={0}` makes the fill's natural width 0: at any
          normal width there is POSITIVE free space, so the fill GROWS to absorb it (clipping its
          dashes) and the title is never shrunk. Only when the pane is narrower than the fixed
          segments + title is there negative space — and then the fill's shrink weight (`shrink ×
          basis = 0`) absorbs none of it, so the title segment alone shrinks and clips (the corners
          stay `flexShrink={0}`, so `╮` always draws). The canonical grow:1/basis:0 fill idiom. */}
      <Box flexGrow={1} flexShrink={1} flexBasis={0} minWidth={0} overflow="hidden">
        <Text color={borderColor} wrap="hard">
          {'─'.repeat(256)}
        </Text>
      </Box>
      {/* Scroll-overflow tail — fixed (never shrinks) so the counts survive a narrow rail (the title
          clips first). BOTH indicators ride the top border: `▴ N` (above) then `▾ N` (below). The
          below indicator was moved here off a separate bottom row that clipped at fractional pane
          heights (see overflowBelow's prop doc). Absent/0 → nothing here → byte-identical to the
          pre-feature top border. */}
      {overflowAbove !== undefined && overflowAbove > 0 && (
        <OverflowIndicator tri={TRI_UP} count={overflowAbove} borderColor={borderColor} />
      )}
      {overflowBelow !== undefined && overflowBelow > 0 && (
        <OverflowIndicator tri={TRI_DOWN} count={overflowBelow} borderColor={borderColor} />
      )}
      <Box flexShrink={0}>
        <Text color={borderColor}>╮</Text>
      </Box>
    </Box>
  );
}

// NOTE: there is no `PaneBorderBottom` row anymore. The bottom border is drawn by Ink's OWN border on
// the {@link ./Pane.tsx Pane}'s content box, which never clips off at fractional pane heights (a
// hand-composed fixed-height bottom ROW did — the `[top, flexGrow, bottom]` split loses its trailing
// cell when the pane rounds to a half-cell, e.g. two panels splitting an odd rail height). The `▾ N`
// scroll-below count that row used to carry now rides {@link PaneBorderTop} beside the `▴ N` count.
