/**
 * `useBodyLayout` — the live wiring of the pure budget engine into the component tree (L3).
 *
 * Reads the live terminal size ({@link useTerminalSize}), the orientation ({@link useOrientation}),
 * and both rails' natural content widths ({@link useRailContent}), and runs {@link computeBodyLayout}
 * to produce the explicit cell budget the Body threads into each `<Rail>` and the `<Stage>` floor.
 *
 * Thin glue (rule 1): no formatting, no bus, no `useInput`. All the math is the pure engine; this hook
 * only injects the live inputs. App calls it once and threads the result down (one source of truth,
 * like the single `useOrientation()` call).
 *
 * The `gap` is 1 to match the App Body's `columnGap`/`rowGap` of 1 between each region — the engine
 * reserves one gap per PRESENT rail so the Stage floor accounts for the inter-region spacing Yoga
 * draws. (Whether a 0-cell present rail still draws a gap is a flex concern verified in L7.)
 *
 * ## Portrait budgets the BODY height, not the terminal rows (L4c / L4c-fix2)
 * In portrait the rails are horizontal strips stacked above/below the Stage WITHIN the Body region —
 * but the Body is NOT the whole terminal: the Shell also draws a topbar, the ChatInput, and a footer,
 * so the Body's true height is `rows − chrome`. Budgeting the rows axis against the full terminal
 * `rows` made portrait content several rows too tall and the bottom strip spilled over the chat input
 * + footer (the L4b overflow). The fix: the caller threads in the Body's real height as `bodyHeight`,
 * and this hook uses THAT as the portrait rows-total so nothing the engine budgets can exceed the real
 * Body height. We do NOT subtract a hardcoded chrome line-count (that would be a forbidden absolute,
 * R5, and would break if the footer wraps) — App derives it by MEASURING the chrome.
 *
 * L4c-fix2 changed HOW App derives that height. The first attempt measured the Body box directly, but
 * the Body is `flexGrow={1}`/`flexBasis={0}` and its measurement RACED the wrapping `BottomBar`: when
 * the footer wrapped to a second line the Body came back 1 row too tall and the bottom strip overlapped
 * the ChatInput by one row. App now measures the two `flexShrink={0}` chrome boxes (TopBar + the
 * ChatInput/BottomBar box) — which are NOT in the flex-grow race, so their content-driven heights are
 * unambiguous — and passes `bodyHeight = rows − topbar − chrome`. Before the first measurement
 * `bodyHeight` is 0 and we fall back to terminal `rows` (self-corrects on the next layout).
 *
 * LANDSCAPE is unchanged: the engine budgets the WIDTH axis (cols) and ignores the rows-total
 * entirely (there is no horizontal chrome to subtract — the Body spans the full width), so we still
 * pass the terminal `rows` there; it is inert.
 */

import type { PanelId } from '../input/panels.js';
import type { BodyLayout } from '../layout/budget.js';
import { computeBodyLayout } from '../layout/budget.js';
import { useRailContent } from '../layout/railContent.js';
import { usePanelStore } from './useInputStores.js';
import { useOrientation } from './useOrientation.js';
import { useTerminalSize } from './useTerminalSize.js';

/** The inter-region gap (cells) the App Body renders between each rail and the Stage. */
const BODY_GAP = 1;

/** The right region's panels (mirrors App's `RIGHT_PANELS`) — used to count the present ones so the
 * engine can derive usage's per-orientation inner width (portrait splits the strip width with crows). */
const RIGHT_PANELS: readonly PanelId[] = ['usage', 'crows'];

/**
 * Compute the live {@link BodyLayout} for the current terminal size, orientation, and rail contents.
 *
 * @param bodyHeight - The MEASURED height (in terminal lines) of the App Body region, used as the
 *   portrait rows-total so the strips + Stage never overflow into the surrounding chrome (L4c).
 *   `0`/omitted (first paint, non-TTY test) → fall back to the terminal `rows`. Ignored in landscape.
 */
export function useBodyLayout(bodyHeight = 0): BodyLayout {
  const { rows, columns } = useTerminalSize();
  const orientation = useOrientation();
  const left = useRailContent('left');
  const right = useRailContent('right');
  // Count the PRESENT right-rail panels so the engine can derive usage's inner width — in portrait the
  // strip lays usage + crows out side-by-side, so usage gets only its SHARE of the strip width (L4d).
  const visible = usePanelStore((s) => s.visible);
  const rightPanelCount = RIGHT_PANELS.filter((id) => visible.has(id)).length;
  // Portrait budgets the rows axis against the measured Body height (not terminal rows) so nothing
  // spills past the Body into the chat input + footer. Landscape ignores this total (it budgets cols).
  const portraitRows = bodyHeight > 0 ? bodyHeight : rows;
  return computeBodyLayout({
    cols: columns,
    rows: orientation === 'portrait' ? portraitRows : rows,
    orientation,
    gap: BODY_GAP,
    left,
    right,
    rightPanelCount,
  });
}
