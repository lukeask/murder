/**
 * `railContent.ts` — the natural-width source (L2): for a side, read the visible panels' rows from
 * the store and report the rail's natural cross-axis size (R2/R6) so {@link computeBodyLayout} can
 * size the rail to "only as wide as the widest ledger element it contains."
 *
 * ## Why width is computed from the FORMATTED view-model, not the raw slice
 * The selectors already do all formatting (rule 2): char counts are right-padded to a fixed field
 * width (`"5,000 chars"` and `"50,000 chars"` are both padded to 15), names are indented, ticket
 * cells are truncated to their column budgets. So the natural width MUST be measured off those
 * formatted strings — that is exactly what keeps the rail width alignment-stable (R10): a row with a
 * "50,000" count is no wider than one with "5,000", because the selector already padded them equal.
 *
 * ## Filename cap (R8)
 * The ONE thing this layer adds on top of the formatted strings is the filename head-clip: a row's
 * name contributes at most {@link FILENAME_CAP} columns to the rail width (`clipName`, keep the head),
 * so one pathologically long filename can never inflate a rail and crowd the Stage. Render-time
 * truncation (`wrap="truncate"`) already keeps the head; this caps the width CONTRIBUTION.
 *
 * ## Title-row width (L3b)
 * A rail's natural width is the `max` of its widest BODY row AND its widest panel TITLE row — the
 * `╭─ ` + title + titleExtra + ` ╮` inline-title border line ({@link ./components/paneBorder.tsx}).
 * Without this, a narrow panel whose title is wider than its rows would size the rail too small and
 * the title would clip (the paneBorder L3b change makes that SAFE — the `╮` always closes — but
 * sizing for the title makes the title FIT without truncation in its common states). The Crows
 * `[min]`/`[max]` suffix is CrowsPanel-LOCAL `useState` this layer can't observe, so we budget the
 * representative widest suffix ({@link CROWS_TITLE_EXTRA}) so the title fits in both modes.
 *
 * ## Portrait content-height (L4b)
 * For portrait the rail is a horizontal STRIP and its needed cross-axis size is HEIGHT, not width.
 * Each panel's content height (in terminal lines) = the inline-title/top-border line (1) + the
 * column-titles header (the panels that have one) + `rows × linesPerEntry` + the bottom border (1).
 * A side's natural height is the MAX over its visible panels (panels sit side-by-side in the strip,
 * so the strip must be as tall as its tallest panel). {@link computeBodyLayout} uses this on the
 * rows axis in portrait so the strip is tall enough for its content and never collides with the chat
 * input / footer below it.
 *
 * ## Per-side reading (R6)
 *  - LEFT rail = plans/notes/reports/tickets. plans/notes/reports share one two-line `.name` row
 *    shape; tickets is a multi-column row whose natural width is its laid-out column widths.
 *  - RIGHT rail = the CROW-LEDGER row width when crows are visible (R6: the right rail is sized to the
 *    crows, and Usage adapts to it via its tiers — L4). When only Usage is on, the rail reserves a
 *    sane usage width; the precise tiered gauge widths are L4 (gated), so this reserves a constant.
 *
 * Two layers, like every selector:
 *  - Pure width functions (`*NaturalWidth`) — no React/store; unit-testable against varied row data.
 *  - A `useRailContent(side)` hook — reads the visible set + the relevant slices and assembles a
 *    {@link RailContent} for {@link computeBodyLayout}.
 */

import { useAppStore } from '../hooks/useAppStore.js';
import { usePanelStore } from '../hooks/useInputStores.js';
import type { PanelId } from '../input/panels.js';
import { useCrowsView } from '../selectors/crowsSelectors.js';
import { useNotesView } from '../selectors/notesSelectors.js';
import { usePlansView } from '../selectors/plansSelectors.js';
import { useReportsView } from '../selectors/reportsSelectors.js';
import { useTicketsView } from '../selectors/ticketsSelectors.js';
import { useUsageView } from '../selectors/usageSelectors.js';
import {
  clipName,
  FILENAME_CAP,
  MIN_PANEL_WIDTH,
  MIN_USAGE_WIDTH,
  type RailContent,
  USAGE_PANE_CHROME,
  USAGE_TIER_LARGE_MIN,
} from './budget.js';

// ---------------------------------------------------------------------------
// Layout constants for the width contribution (mirror the panels' rendered gutters/labels)
// ---------------------------------------------------------------------------

/**
 * The leading gutter every doc panel reserves on a row's first line: cursor marker(1) + space(1) +
 * fixed-width star gutter(2) = 4 columns (see PlansPanel/NotesPanel/ReportsPanel `renderEntry`). The
 * name follows it, so the row's line-1 width is `DOC_ROW_GUTTER + cappedName.length`.
 */
const DOC_ROW_GUTTER = 4;

/**
 * The leading gutter a crow row reserves: health glyph(1) + space(1) = 2 columns (see CrowsPanel
 * `renderCrowRow`). The name + `  ` + status follow it.
 */
const CROW_ROW_GUTTER = 2;

/** Columns between a crow's name and its status (`  ${row.name}  ` + status). */
const CROW_NAME_STATUS_GAP = 2;

/**
 * The right-rail width usage reserves when it drives the rail ALONE (no crows). usage-alone should
 * show its FULLEST form (the large tier — R9), so we reserve the rail width that yields a large-tier
 * inner width: `USAGE_TIER_LARGE_MIN(33)` of gauge glyphs + `USAGE_PANE_CHROME(4)` of Pane border +
 * padding = 37 (L4d, problem 2). The engine still compresses this toward the budget on a narrow
 * terminal (the Stage keeps its ≥60% floor), so usage drops to medium/mini there via the tiers; this
 * only sets the *desired* natural width so a wide terminal renders large instead of medium. Floored at
 * {@link MIN_USAGE_WIDTH} (degenerate guard; the large reserve always exceeds it).
 */
const USAGE_RESERVE_WIDTH = Math.max(MIN_USAGE_WIDTH, USAGE_TIER_LARGE_MIN + USAGE_PANE_CHROME);

// ---------------------------------------------------------------------------
// Title-row width (L3b) — the inline-title top-border line each Pane draws
// ---------------------------------------------------------------------------

/**
 * Fixed chrome the inline-title border line adds around the title text: the leading `╭─ ` (3) plus
 * the trailing ` ╮` (1 space + the corner = 2) = 5 columns (see {@link ../components/paneBorder.tsx}).
 * The title's natural width is this plus the title + titleExtra text.
 */
const TITLE_CHROME_WIDTH = '╭─ '.length + ' ╮'.length;

/**
 * The Crows `titleExtra` suffix this layer budgets for. The actual `[min]`/`[max]` mode label is
 * CrowsPanel-LOCAL `useState` we can't observe, so we reserve the WIDEST it renders — `' [max]'` and
 * `' [min]'` are both 6 columns (leading space + 5) — so the title fits in BOTH modes without
 * truncation. (Over-reserving is harmless: the rail width is a `max` over rows + this title row.)
 */
const CROWS_TITLE_EXTRA = ' [max]';

/** The width of a panel's inline-title border row: `╭─ ` + title + titleExtra + ` ╮` (L3b).
 * Exported so the title-overflow guard is unit-testable independently of the store-reading hook. */
export function titleRowWidth(title: string, titleExtra = ''): number {
  return TITLE_CHROME_WIDTH + title.length + titleExtra.length;
}

// ---------------------------------------------------------------------------
// Per-panel content height (L4b) — terminal lines a panel's body needs in portrait
// ---------------------------------------------------------------------------

/**
 * The two border lines every Pane draws: the inline-title/top-border row (1) and the bottom border
 * row (1). The title row and the top border are the SAME line ({@link ../components/paneBorder.tsx} —
 * the title is painted ON the top border), so this is 2, not 3 — do not double-count a title row.
 */
const PANE_BORDER_LINES = 2;

/** Plans/Notes/Reports/Tickets render a 2-line entry (`linesPerEntry=2`). */
const DOC_LINES_PER_ENTRY = 2;

/**
 * A list panel's content height in lines: the 2 border lines + a column-titles header (the panels
 * that pass one render it at `linesPerEntry` tall) + `rowCount × linesPerEntry` body lines. Overflow
 * `…` indicators appear only when the panel is CLAMPED shorter than this, so they are excluded from
 * the natural (unclamped) height. When the list is EMPTY the panels short-circuit to a single chrome
 * line ("no plans") BEFORE the Ledger — so there is no header line either; an empty panel is just
 * `borders + 1`. Pure — unit-testable.
 */
export function listNaturalHeight(
  rowCount: number,
  linesPerEntry: number,
  hasHeader: boolean,
): number {
  if (rowCount <= 0) {
    return PANE_BORDER_LINES + 1; // empty → one "no rows" chrome line, no Ledger + no header
  }
  const headerLines = hasHeader ? linesPerEntry : 0;
  return PANE_BORDER_LINES + headerLines + rowCount * linesPerEntry;
}

/**
 * The Usage panel's content height. Usage is NOT a Ledger (no `linesPerEntry`); it renders a single
 * column-titles key line + one harness-header line per provider group + one gauge line per window
 * (see {@link ../components/UsagePanel.tsx} `UsageBody`). Plus the 2 Pane border lines. Empty → the
 * 2 borders + one chrome line. Pure.
 */
export function usageNaturalHeight(
  groups: readonly { readonly gauges: readonly unknown[] }[],
): number {
  if (groups.length === 0) {
    return PANE_BORDER_LINES + 1; // "no usage data" chrome line
  }
  const keyLine = 1; // the UsageKeyLine column-titles row
  let bodyLines = keyLine;
  for (const group of groups) {
    bodyLines += 1 + group.gauges.length; // harness header + one line per gauge window
  }
  return PANE_BORDER_LINES + bodyLines;
}

// ---------------------------------------------------------------------------
// Pure width functions — measured off the FORMATTED view-model strings
// ---------------------------------------------------------------------------

/** The wider of a row's two lines for a doc panel (plans/notes/reports). Name is capped (R8). */
function docRowWidth(name: string, line2: string): number {
  // Line 1: gutter + capped name. Line 2: the fixed metadata (already alignment-padded by the selector).
  // The name is clipped to its CONTRIBUTION cap, so a long filename can't inflate the rail (R8).
  const line1 = DOC_ROW_GUTTER + clipName(name, FILENAME_CAP).length;
  return Math.max(line1, line2.length);
}

/**
 * Natural width of a doc-panel rail body: the max row width across all rows, each measured off the
 * formatted view-model lines (the name capped). Returns 0 for an empty list. Pure — unit-testable.
 */
export function docNaturalWidth(
  rows: readonly {
    readonly name: string;
    readonly charCount: string;
    readonly updatedAt: string;
  }[],
): number {
  let max = 0;
  for (const row of rows) {
    // Line 2 mirrors the panel's `    ${charCount} · ${updatedAt}` (4-space indent + " · " join).
    const line2 = `    ${row.charCount} · ${row.updatedAt}`;
    const w = docRowWidth(row.name, line2);
    if (w > max) {
      max = w;
    }
  }
  return max;
}

/**
 * Natural width of the tickets rail body. Tickets is a multi-column Ledger (up to 5 columns); its
 * natural width is the laid-out column block. We measure off the formatted cells: column 1 is
 * `gutter + max(idCell, titleCell)` and the remaining four columns each contribute their widest cell.
 * The Ledger drops trailing columns as the measured width shrinks (`columnsForWidth`), so reporting
 * the FULL multi-column width here lets the engine compress the rail and the columns fall away (R3).
 */
export function ticketsNaturalWidth(
  rows: readonly {
    readonly idCell: string;
    readonly titleCell: string;
    readonly statusCell: string;
    readonly lastUpdateCell: string;
    readonly depsCell: string;
    readonly scheduleCell: string;
    readonly harnessCell: string;
    readonly modelCell: string;
    readonly planCell: string;
    readonly worktreeCell: string;
  }[],
): number {
  // Each column is two stacked cells; the column's width is the wider of the two. Sum the columns +
  // the leading gutter (marker + space) + one space between columns.
  const cols = [
    (r: (typeof rows)[number]) => Math.max(r.idCell.length, r.titleCell.length),
    (r: (typeof rows)[number]) => Math.max(r.statusCell.length, r.lastUpdateCell.length),
    (r: (typeof rows)[number]) => Math.max(r.depsCell.length, r.scheduleCell.length),
    (r: (typeof rows)[number]) => Math.max(r.harnessCell.length, r.modelCell.length),
    (r: (typeof rows)[number]) => Math.max(r.planCell.length, r.worktreeCell.length),
  ];
  const colWidths = cols.map((widthOf) => {
    let max = 0;
    for (const r of rows) {
      const w = widthOf(r);
      if (w > max) {
        max = w;
      }
    }
    return max;
  });
  const present = colWidths.filter((w) => w > 0);
  if (present.length === 0) {
    return 0;
  }
  const gutter = 2; // marker + space (TicketsPanel's leading gutter)
  const interColumnGaps = present.length - 1; // one space between adjacent columns
  return gutter + interColumnGaps + present.reduce((a, b) => a + b, 0);
}

/**
 * Natural width of the crow-ledger rail body (R6 — this is what sizes the right rail when crows are
 * on). Crows is a single-column Ledger; a crow row is `glyph(1) + space(1) + name + "  " + status`,
 * and the maximized second line is `"  " + harness + " · " + model`. Section header rows are the bold
 * group label. The name is capped (R8). Returns 0 for an empty view.
 */
export function crowNaturalWidth(
  sections: readonly {
    readonly label: string;
    readonly rows: readonly {
      readonly name: string;
      readonly status: string;
      readonly harness: string;
      readonly model: string;
    }[];
  }[],
  expanded: boolean,
): number {
  let max = 0;
  for (const section of sections) {
    if (section.label.length > max) {
      max = section.label.length;
    }
    for (const row of section.rows) {
      const line1 =
        CROW_ROW_GUTTER +
        clipName(row.name, FILENAME_CAP).length +
        CROW_NAME_STATUS_GAP +
        row.status.length;
      // The maximized second line: `  ${harness} · ${model}` (2-space indent + " · " join).
      const line2 = expanded ? 2 + row.harness.length + 3 + row.model.length : 0;
      const w = Math.max(line1, line2);
      if (w > max) {
        max = w;
      }
    }
  }
  return max;
}

/**
 * Natural HEIGHT of the crows panel in lines (L4b — portrait). The CrowsPanel flattens its sections
 * into ONE Ledger row list: a header row per section plus one crow row per crow, all uniform at
 * `linesPerEntry` (1 minimized / 2 maximized — see {@link ../components/CrowsPanel.tsx}). It passes a
 * column-titles `header` (the "crow · status" key), so the header line costs `linesPerEntry` too. The
 * `expanded` flag is CrowsPanel-LOCAL state we can't observe, so callers pass the WIDEST mode (2,
 * matching the width side's expanded reserve) for a stable strip height across the min/max toggle.
 * Empty view → the 2 border lines + one "no crows" chrome line. Pure.
 */
export function crowNaturalHeight(
  sections: readonly { readonly rows: readonly unknown[] }[],
  expanded: boolean,
): number {
  let flatRows = 0;
  for (const section of sections) {
    flatRows += 1 + section.rows.length; // one header row + its crow rows
  }
  const linesPerEntry = expanded ? 2 : 1;
  return listNaturalHeight(flatRows, linesPerEntry, true);
}

// ---------------------------------------------------------------------------
// The hook — assemble a side's RailContent from the visible set + the live slices
// ---------------------------------------------------------------------------

/** The left region's panels in screen order (mirrors App's `LEFT_PANELS`). */
const LEFT_PANELS: readonly PanelId[] = ['plans', 'notes', 'reports', 'tickets'];
/** The right region's panels in screen order (mirrors App's `RIGHT_PANELS`). */
const RIGHT_PANELS: readonly PanelId[] = ['usage', 'crows'];

/**
 * Read the live {@link RailContent} for one side. `present` is true iff any of the side's panels are
 * toggled on; `naturalWidth` is the max over the visible panels of `max(widest body row, title row)`
 * (R2 + L3b), with the right side driven by the crow-ledger width when crows are on (R6);
 * `naturalHeight` is the MAX over the visible panels of each panel's content height in lines (L4b),
 * for the portrait rows-axis budget.
 *
 * This hook subscribes to the visible set + the slices it needs, so the body layout re-derives when a
 * panel is toggled, the data changes, or the crows mode flips. The width/height math itself is the
 * pure functions above (rule 2 — the formatting/measurement lives outside the component tree); this
 * hook is the live-data injection point, mirroring the `use*View` selector hooks.
 *
 * NOTE on the crows `expanded` flag: that toggle is the CrowsPanel's local `useState`, not in the
 * store, so this hook cannot observe it. We size the right rail to the EXPANDED crow width AND height
 * (the larger of the two modes) so the rail never has to grow when the user maximizes a crow row — a
 * stable rail size across the min/max toggle. Documented for L7: verify the rail doesn't look
 * over-wide/-tall in the minimized default; if so, this is the one knob to revisit.
 *
 * NOTE on the present-rail floor: a present rail's natural width is floored at {@link MIN_PANEL_WIDTH}
 * (its smallest legible form, R7). Without this, an EMPTY or still-LOADING panel computes width 0 and,
 * sized to 0 cells, would vanish entirely (no title, no "no plans" chrome) — which also flashes on
 * first paint before the slices load. Flooring HERE (not in the engine) keeps the engine's
 * slack/compress logic uniform over a "natural ≥ smallest legible form" input. The HEIGHT side needs
 * no such floor: `listNaturalHeight`/`usageNaturalHeight` already return `borders + 1` for an empty
 * panel, so a present-but-empty strip is never 0 lines.
 */
export function useRailContent(side: 'left' | 'right'): RailContent {
  const visible = usePanelStore((s) => s.visible);
  const panels = side === 'left' ? LEFT_PANELS : RIGHT_PANELS;
  const present = panels.some((id) => visible.has(id));

  // Subscribe to every slice this side could read — unconditionally, so hook order is stable across
  // renders (rules of hooks). The selectors are memoised on slice identity, so an unused subscription
  // is cheap (it only re-runs when its own slice ref-changes).
  const plans = useAppStore((s) => s.plans);
  const favorites = useAppStore((s) => s.favorites);
  const notes = useAppStore((s) => s.notes);
  const reports = useAppStore((s) => s.reports);
  const tickets = useAppStore((s) => s.tickets);
  const roster = useAppStore((s) => s.roster);
  const usage = useAppStore((s) => s.usage);

  const plansView = usePlansView(plans, favorites);
  const notesView = useNotesView(notes, favorites);
  const reportsView = useReportsView(reports, favorites);
  const ticketsView = useTicketsView(tickets);
  const crowsView = useCrowsView(roster);
  const usageView = useUsageView(usage);

  if (side === 'left') {
    let naturalWidth = 0;
    let naturalHeight = 0;
    if (visible.has('plans')) {
      // Width = max(widest body row, title row) (L3b); height = the panel's content lines (L4b).
      naturalWidth = Math.max(
        naturalWidth,
        docNaturalWidth(plansView.rows),
        titleRowWidth('Plans'),
      );
      naturalHeight = Math.max(
        naturalHeight,
        listNaturalHeight(plansView.rows.length, DOC_LINES_PER_ENTRY, true),
      );
    }
    if (visible.has('notes')) {
      naturalWidth = Math.max(
        naturalWidth,
        docNaturalWidth(notesView.rows),
        titleRowWidth('Notes'),
      );
      naturalHeight = Math.max(
        naturalHeight,
        listNaturalHeight(notesView.rows.length, DOC_LINES_PER_ENTRY, true),
      );
    }
    if (visible.has('reports')) {
      naturalWidth = Math.max(
        naturalWidth,
        docNaturalWidth(reportsView.rows),
        titleRowWidth('Reports'),
      );
      naturalHeight = Math.max(
        naturalHeight,
        listNaturalHeight(reportsView.rows.length, DOC_LINES_PER_ENTRY, true),
      );
    }
    if (visible.has('tickets')) {
      naturalWidth = Math.max(
        naturalWidth,
        ticketsNaturalWidth(ticketsView.rows),
        titleRowWidth('Tickets'),
      );
      naturalHeight = Math.max(
        naturalHeight,
        listNaturalHeight(ticketsView.rows.length, DOC_LINES_PER_ENTRY, true),
      );
    }
    // Floor a present rail's WIDTH at its smallest legible form (R7) so an empty/loading panel still
    // shows its title + chrome instead of collapsing to 0 cells. Height needs no floor (see above).
    return {
      naturalWidth: present ? Math.max(naturalWidth, MIN_PANEL_WIDTH) : 0,
      naturalHeight: present ? naturalHeight : 0,
      present,
    };
  }

  // Right side (R6): the crow-ledger width drives the rail when crows are on; Usage adapts to it via
  // its tiers (L4), so it does NOT widen the rail — it only reserves its smallest legible form when it
  // is the ONLY panel on the right. Height, however, IS the tallest of the present panels (a strip in
  // portrait must hold both usage AND crows side-by-side), so both contribute to `naturalHeight`.
  let naturalWidth = 0;
  let naturalHeight = 0;
  if (visible.has('crows')) {
    naturalWidth = Math.max(naturalWidth, crowNaturalWidth(crowsView.sections, true));
    naturalWidth = Math.max(naturalWidth, titleRowWidth('Crows', CROWS_TITLE_EXTRA));
    naturalHeight = Math.max(naturalHeight, crowNaturalHeight(crowsView.sections, true));
  }
  if (visible.has('usage')) {
    // Usage never widens the rail (R6 — it adapts via tiers), but it DOES set the strip height in
    // portrait, and its title must fit when it is the only right panel.
    naturalHeight = Math.max(naturalHeight, usageNaturalHeight(usageView.groups));
    if (!visible.has('crows')) {
      naturalWidth = Math.max(naturalWidth, USAGE_RESERVE_WIDTH, titleRowWidth('Usage'));
    }
  }
  // Floor a present rail's WIDTH at its smallest legible form (R7) — same reasoning as the left side;
  // the usage-only reserve already exceeds this, so the floor only bites the empty-crows case.
  return {
    naturalWidth: present ? Math.max(naturalWidth, MIN_PANEL_WIDTH) : 0,
    naturalHeight: present ? naturalHeight : 0,
    present,
  };
}
