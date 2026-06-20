/**
 * ResourceRow — the single source of the two-line row for the doc-style resource lists
 * (Plans / Notes / Reports). Each panel feeds its already-projected fields through {@link
 * renderResourceEntry} (passed to a {@link ./Ledger.tsx Ledger} as `renderEntry`) and labels them
 * with {@link renderResourceHeader} (the `header` prop).
 *
 * The three panels drifted before this extraction: notes/reports had grown a fixed-width star gutter
 * and a 3-space line-2 indent, pushing every row right of plans' flush-left layout. This module is
 * plans' canonical render — flush left, star shown only when starred, no forced cursor glyph.
 *
 * Per-panel variation stays in each SELECTOR, not here: the plans tree indent (4 spaces baked into a
 * child's `name`), the recency/star sort, and the starred-to-top float all happen before the row
 * reaches this renderer. By the time `row.name` arrives, it is exactly the string to paint — this
 * renderer adds no indent of its own.
 */

import { Box, Text } from 'ink';
import type { LedgerEntryContext } from './Ledger.js';

/**
 * The display-ready fields one resource row paints. Produced by each panel's selector; this renderer
 * treats them as final strings (no further formatting or indentation).
 */
export interface ResourceRowFields {
  /** Display name, already indented by the selector if nested (e.g. a child plan) — renderer adds none. */
  readonly name: string;
  /** Char count formatted as a compact display string. */
  readonly charCount: string;
  /** `updated_at` formatted `Mon. dd HH:MM` (e.g. `Jun. 10 09:32`). */
  readonly updatedAt: string;
  /** Whether this resource is starred (in the explicit favorite set). The renderer shows a ★ prefix. */
  readonly starred: boolean;
}

/**
 * Glyph painted in the cursor row's first column. Currently a plain space — the Ledger's full-width
 * highlight marks the selection on its own, and a glyph here renders as a half-block in the default
 * foreground over the highlight (a stray off-color cell). Set back to `'▌'` (or any glyph) to restore
 * a left-edge accent bar across ALL THREE doc panels at once; the gutter is already reserved (1 col)
 * so re-enabling is this one line.
 */
const CURSOR_GLYPH = ' ';

/**
 * Render one resource row as a two-line Ledger entry. Line 1: optional cursor marker + optional star +
 * the (already-projected) name. Line 2: char count · updated time. The Ledger paints the full-width
 * selection background and the alternating-row shade, so this only uses `ctx.selected` for the line-2
 * dim (and the marker, when the glyph is enabled) — it does NOT set `inverse` (that would fight the
 * Ledger's background). Single column (`maxColumns=1`), so `ctx.columns` is unused. Memo-free.
 */
export function renderResourceEntry(
  row: ResourceRowFields,
  ctx: LedgerEntryContext,
): React.ReactNode {
  // Cursor marker occupies a column ONLY when the glyph feature is enabled. Disabled (the default —
  // CURSOR_GLYPH is a space), it contributes NOTHING, so a top-level row's name sits flush at the
  // left edge (no spurious indent); selection is shown by the Ledger's full-width highlight. Re-enable
  // by setting CURSOR_GLYPH to '▌': the selected row then gets the glyph and others a 1-col space.
  const marker = CURSOR_GLYPH === ' ' ? '' : ctx.selected ? CURSOR_GLYPH : ' ';
  // Star prefix shown ONLY when starred — no fixed-width reservation. A reserved gutter would indent
  // every row (the spurious indent we're removing), so unstarred rows start right at the name and the
  // child indent (baked into `row.name` by the selector) is the ONLY indent — one level for a child.
  const star = row.starred ? '★ ' : '';
  return (
    // The LedgerRow wraps this in a `row` Box (with the full-width highlight/alt-bg background), so a
    // two-line entry must compose its own `column` here. `flexGrow={1}` lets the background span the
    // full row width behind both lines; `flexShrink={0}` so Yoga doesn't sample/drop a line. Line 1 is
    // `[marker][star]name` with marker/star present only when active; line 2 + header sit flush left.
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${marker}${star}${row.name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
}

/**
 * The Ledger column-titles key — a dim two-line block (matching `linesPerEntry=2`) labeling what the
 * entry lines mean: `name` over `size · updated`. Both labels sit flush left, matching the entry's
 * flush-left line 2 (line 1 carries the optional marker/star gutter; line 2 and the header do not).
 * Shared by all three doc panels via the `header` prop. (`columns` is unused — these panels are
 * single-column; a multi-column panel would key each field per `columns`.)
 */
export function renderResourceHeader(): React.ReactNode {
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>{'name'}</Text>
      <Text dimColor>{'size · updated'}</Text>
    </Box>
  );
}
