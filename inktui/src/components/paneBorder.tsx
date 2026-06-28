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
import {
  PANE_BORDER_GLYPHS,
  type PaneBorderGlyphs,
  SCROLL_THUMB,
  TRI_DOWN,
  TRI_UP,
} from './glyphs.js';

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
  horizontal,
}: {
  readonly tri: string;
  readonly count: number;
  readonly borderColor: string;
  readonly horizontal: string;
}): React.JSX.Element {
  return (
    <Box flexShrink={0}>
      <Text color={borderColor}>{`${horizontal} ${tri} `}</Text>
      <Text color={borderColor} dimColor>
        {String(count)}
      </Text>
      <Text color={borderColor}>{` ${horizontal}${horizontal}`}</Text>
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
  /** Hand-composed border glyphs (`round` blurred / `bold` focused) — see {@link ./glyphs.js}. */
  readonly glyphs: PaneBorderGlyphs;
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
  glyphs = PANE_BORDER_GLYPHS.round,
  bold = false,
  titleExtra,
  overflowAbove,
  overflowBelow,
}: PaneBorderTopProps): React.JSX.Element {
  const { topLeftPrefix, topRight, horizontal } = glyphs;
  return (
    // No rigid `width="100%"`: the row STRETCHES to the (shrinkable, `minWidth={0}`) outer Pane box on
    // the cross axis, so on a narrow tiled column it resolves to the SAME width as the content box below
    // rather than pinning to a fixed 100% that the content then undercuts. `minWidth={0}` lets the
    // fixed corner/title segments shrink-flow correctly; `overflow="hidden"` clips the `─` fill overrun.
    <Box flexDirection="row" flexShrink={1} minWidth={0} overflow="hidden" height={1}>
      <Box flexShrink={0}>
        <Text color={borderColor}>{topLeftPrefix}</Text>
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
          {horizontal.repeat(256)}
        </Text>
      </Box>
      {/* Scroll-overflow tail — fixed (never shrinks) so the counts survive a narrow rail (the title
          clips first). BOTH indicators ride the top border: `▴ N` (above) then `▾ N` (below). The
          below indicator was moved here off a separate bottom row that clipped at fractional pane
          heights (see overflowBelow's prop doc). Absent/0 → nothing here → byte-identical to the
          pre-feature top border. */}
      {overflowAbove !== undefined && overflowAbove > 0 && (
        <OverflowIndicator
          tri={TRI_UP}
          count={overflowAbove}
          borderColor={borderColor}
          horizontal={horizontal}
        />
      )}
      {overflowBelow !== undefined && overflowBelow > 0 && (
        <OverflowIndicator
          tri={TRI_DOWN}
          count={overflowBelow}
          borderColor={borderColor}
          horizontal={horizontal}
        />
      )}
      <Box flexShrink={0}>
        <Text color={borderColor}>{topRight}</Text>
      </Box>
    </Box>
  );
}

export interface PaneBorderBottomProps {
  /** Border color — the spacer cells inherit it (they're blanks, so it's only meaningful if a future
   *  glyph rides them); the left/right nodes own their own color. */
  readonly borderColor: string;
  /** Hand-composed border glyphs for the footer dash segments. */
  readonly glyphs?: PaneBorderGlyphs;
  /** Optional left-anchored node placed after the `╰─ ` corner (the bottom mirror of the title slot).
   *  SHRINKABLE + clipped. The CALLER owns its color. */
  readonly leftExtra?: React.ReactNode;
  /** Optional right-anchored node placed before the ` ─╯` corner, so bottom-right info sits the same
   *  distance from the right edge as the title sits from the left (the `╭─ ` / ` ─╯` 3-cell mirror).
   *  FIXED (`flexShrink={0}`). The CALLER owns its color. */
  readonly rightExtra?: React.ReactNode;
}

/**
 * The inline bottom-border footer — the mirror of {@link PaneBorderTop}, used by a {@link ./Pane.tsx
 * Pane} that opts into a footer (and by {@link ./ChatInput.tsx ChatInput}). Reads as
 * `╰─ <left> ──…── <right> ─╯`, carrying e.g. a chat pane's `Claude Code ◇ Opus 4.8`.
 *
 * ## Why this is an OVERLAY, not a row (the fractional-height fix)
 * The bottom border is NOT a separate fixed-height row appended after the content — that shape
 * (`[top(1), content(flex), footer(1)]`) drops the footer when the pane's height rounds to a
 * half-cell (two panes splitting an odd grid height; the documented bug that once led to this
 * component being deleted). Instead the CALLER keeps Ink's OWN bottom border on its content box —
 * which is border-box reserved space and so is robust at any fractional height — and this footer is
 * pulled UP onto that border line with `marginTop={-1}`. Because the overlay's height (1) and its
 * negative margin (−1) cancel, it contributes ZERO height: the pane is exactly as tall as it would be
 * with no footer (the clip-robust case), so nothing ever clips. The overlay's empty cells are
 * transparent, so Ink's `╰────╯` shows through everywhere the labels don't paint; the labels (opaque
 * text) overwrite the dashes they sit on. The 2-cell left reserve sits over `╰─`, the 1-cell right
 * reserve over the closing `╯` (Ink's own, or the scrollbar column's), so the corners are never
 * clobbered.
 */
export function PaneBorderBottom({
  borderColor,
  glyphs = PANE_BORDER_GLYPHS.round,
  leftExtra,
  rightExtra,
}: PaneBorderBottomProps): React.JSX.Element {
  const { horizontal } = glyphs;
  const hasLeft = leftExtra !== undefined && leftExtra !== null && leftExtra !== false;
  const hasRight = rightExtra !== undefined && rightExtra !== null && rightExtra !== false;
  return (
    // No rigid `width="100%"`: like PaneBorderTop, the overlay STRETCHES to the shrinkable outer Pane box
    // so it resolves to the SAME width as the content box it overlays (Ink's `╰────╯` bottom border, or
    // the scrollbar column's `╯`). A fixed 100% would keep the `─╯` reserve at the outer width even after
    // the content box shrank on a narrow tile, landing `╯` past the real right edge → the wrap bug.
    <Box
      flexDirection="row"
      flexShrink={1}
      minWidth={0}
      height={1}
      marginTop={-1}
      overflow="hidden"
    >
      {/* Transparent over Ink's `╰─` corner + dash (the mirror of the top's `╭─ `). */}
      <Box flexShrink={0} width={2} />
      {hasLeft && (
        // ` <label> ` — the surrounding blanks sit over the border `─`, giving the label breathing
        // room exactly like the title's ` ` gaps. SHRINKABLE so a wide label clips before the corners.
        <Box flexShrink={1} minWidth={0} overflow="hidden" flexDirection="row">
          <Text color={borderColor}> </Text>
          {leftExtra}
          <Text color={borderColor}> </Text>
        </Box>
      )}
      {/* Transparent fill — Ink's `─` border shows through (no painted glyphs to clip/flicker). */}
      <Box flexGrow={1} flexShrink={1} flexBasis={0} minWidth={0} overflow="hidden" />
      {hasRight && (
        // ` <label> ─` ending one cell before the right edge; the reserve below shows the `╯` corner,
        // so this reads `… <label> ─╯`, mirroring the title's `╭─ ` 3-cell inset. FIXED — fill clips first.
        <Box flexShrink={0} flexDirection="row">
          <Text color={borderColor}> </Text>
          {rightExtra}
          <Text color={borderColor}>{` ${horizontal}`}</Text>
        </Box>
      )}
      {/* Transparent over the closing `╯` (Ink's own corner, or the scrollbar column's). */}
      <Box flexShrink={0} width={1} />
    </Box>
  );
}

/** The thumb glyph: a full block, so the scroll position reads clearly against the track. */
const THUMB = SCROLL_THUMB;
/** The track glyph — Ink's round/bold border side, so a thumb-less column matches the content box. */
function trackGlyph(glyphs: PaneBorderGlyphs): string {
  return glyphs.vertical;
}

/**
 * The scrollbar-as-right-border column for a scrollable {@link ./Pane.tsx Pane} — the pane's RIGHT
 * border doubles as the scroll track: the thumb is a full `█` run rolling along the light `│` side,
 * and the column's last cell is the `╯` bottom corner (the content box disables its own right border
 * via `borderRight={false}`, so its Ink bottom border ends `╰──` and this corner completes it). This
 * replaced the old separate 1-char scrollbar column inside the border (`█` over a dim track).
 *
 * `height` is the pane's measured inner row count (the same fill-box measurement that drives the
 * window), so the column draws `height` track cells + the corner = exactly the content box's height.
 * A `null` thumb (content fits) draws a plain `│` track — visually just the border. Pure function of
 * its props (rule 1); the thumb geometry is {@link ./panes/docWindow.js computeScrollThumb}.
 */
export function PaneBorderRight({
  height,
  thumb,
  color,
  glyphs = PANE_BORDER_GLYPHS.round,
}: {
  readonly height: number;
  readonly thumb: { readonly size: number; readonly offset: number } | null;
  readonly color: string;
  readonly glyphs?: PaneBorderGlyphs;
}): React.JSX.Element {
  const track = trackGlyph(glyphs);
  const cells = Array.from({ length: Math.max(height, 0) }, (_, i) =>
    thumb !== null && i >= thumb.offset && i < thumb.offset + thumb.size ? THUMB : track,
  );
  return (
    <Box flexDirection="column" width={1} flexShrink={0} minHeight={0} overflow="hidden">
      {cells.map((glyph, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length border cells are position-keyed.
        <Text key={i} color={color}>
          {glyph}
        </Text>
      ))}
      <Text color={color}>{glyphs.bottomRight}</Text>
    </Box>
  );
}
