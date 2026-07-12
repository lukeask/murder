/**
 * DocumentSurface — store-free read-only document pane.
 *
 * Accepts explicit allocated `width`/`height` and display-ready document data. Controllers own
 * store reads, keymaps, and input; this surface owns the visible window and Pane chrome.
 *
 * Body rows are always physical terminal rows: `full` / `compact` / `minimal` wrap before
 * windowing; only `micro` truncates. Never combine logical-line windowing with `Text wrap="wrap"`.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { useTheme } from '../../theme/themeStore.js';
import { terminalSafeText } from '../../utils/terminalSafeText.js';
import { truncateToWidth, wrapTextToRows } from '../../utils/wrapText.js';
import { Pane } from '../Pane.js';
import { computeDocumentWindow, computeScrollThumb } from './shared/scrollWindow.js';

/** Horizontal chrome: side borders only; the scrollbar is the right border, not content gutter. */
const CHROME_WIDTH = 2;
/** Vertical chrome: inline title row + bottom border. */
const CHROME_HEIGHT = 2;
/** Fixed top-border chrome: `╭─ ` + ` ╮`. */
const TITLE_CHROME_WIDTH = 5;
export type DocKind = 'plan' | 'note' | 'report';

export type DocumentDisplayMode = 'full' | 'compact' | 'minimal' | 'micro';

export interface DocumentSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly title: string;
  readonly lines: readonly string[];
  readonly scroll: number;
  readonly gotoPending?: string | null;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

function contentInnerWidth(width: number): number {
  return Math.max(1, width - CHROME_WIDTH);
}

export function documentContentInnerHeight(height: number): number {
  return Math.max(0, height - CHROME_HEIGHT);
}

/** Derive doctype from a `.murder/<dir>/…` fixture or live doc path. */
export function docKindFromTitle(title: string): DocKind {
  if (title.includes('/notes/')) {
    return 'note';
  }
  if (title.includes('/reports/')) {
    return 'report';
  }
  return 'plan';
}

/** Basename without path prefix or `.md` suffix. */
export function docBasename(title: string): string {
  const withoutExt = title.replace(/\.md$/i, '');
  return withoutExt.split('/').pop() ?? title;
}

/**
 * Pane border title for the allocated width — name rule: names <=6 chars show in full; longer names
 * keep ≥6 leading characters when truncated; at extreme narrow widths collapse to `…`.
 */
export function formatDocBorderTitle(
  name: string,
  width: number,
  mode: DocumentDisplayMode,
): string {
  if (mode === 'micro') {
    const titleBudget = Math.max(0, width - TITLE_CHROME_WIDTH);
    if (titleBudget <= 1) {
      return '…';
    }
    if (name.length <= titleBudget) {
      return name;
    }
    if (name.length <= 6) {
      return name.slice(0, titleBudget);
    }
    if (titleBudget >= 7) {
      return `${name.slice(0, 6)}…`;
    }
    if (titleBudget >= 2) {
      return `${name.slice(0, titleBudget - 1)}…`;
    }
    return '…';
  }

  const titleBudget = Math.max(0, width - TITLE_CHROME_WIDTH);
  if (name.length <= titleBudget) {
    return name;
  }
  if (name.length <= 6) {
    return name.slice(0, titleBudget);
  }
  if (titleBudget >= 7) {
    return `${name.slice(0, 6)}…`;
  }
  if (titleBudget >= 2) {
    return `${name.slice(0, titleBudget - 1)}…`;
  }
  return '…';
}

/** Deterministic 2D layout router — branches on allocated size before rendering. */
export function layout(width: number, height: number): DocumentDisplayMode {
  const innerW = contentInnerWidth(width);
  const innerH = documentContentInnerHeight(height);

  if (innerH < 1 || innerW < 6) {
    return 'micro';
  }
  if (innerH < 2) {
    return 'minimal';
  }
  if (innerW < 16) {
    return 'compact';
  }
  return 'full';
}

/**
 * Expand document source lines into physical terminal rows for the allocated pane size.
 * `full` / `compact` / `minimal` wrap within the pane; only `micro` truncates to one row.
 */
export function documentPhysicalRows(
  lines: readonly string[],
  width: number,
  height: number,
): readonly string[] {
  const mode = layout(width, height);
  const columns = contentInnerWidth(width);
  if (mode === 'micro') {
    return lines.map((line) => truncateToWidth(line, columns));
  }
  // Soft-wrap on spaces when possible; hard-break long tokens (URLs) so rows stay ≤ columns.
  return lines.flatMap((line) => wrapTextToRows(line, columns, { hard: true, wordWrap: true }));
}

function windowRowCount(innerH: number): number {
  return Math.max(0, innerH);
}

export const DocumentSurface = memo(function DocumentSurface({
  width,
  height,
  focused,
  title,
  lines,
  scroll,
  gotoPending = null,
  status = 'ready',
  error = null,
}: DocumentSurfaceProps): React.JSX.Element {
  const theme = useTheme();
  const mode = layout(width, height);
  const innerH = documentContentInnerHeight(height);
  const windowRows = windowRowCount(innerH);
  const physicalRows = useMemo(
    () => documentPhysicalRows(lines, width, height),
    [lines, width, height],
  );
  const { start, end } = computeDocumentWindow(
    physicalRows.length,
    scroll,
    Math.max(windowRows, 1),
  );
  const window = windowRows > 0 ? physicalRows.slice(start, end) : [];
  const thumb = windowRows > 0 ? computeScrollThumb(physicalRows.length, start, windowRows) : null;
  const basename = docBasename(title);
  const baseBorderTitle = formatDocBorderTitle(basename, width, mode);
  const kind = docKindFromTitle(title);
  const showScrollbar = windowRows > 0 && mode !== 'micro';

  const body = (() => {
    if (windowRows === 0) {
      return null;
    }
    if (status === 'error' && error !== null) {
      return (
        <Text color={theme.error} wrap="truncate">
          {terminalSafeText(`error: ${error}`)}
        </Text>
      );
    }
    if (status === 'loading') {
      return <Text dimColor>loading…</Text>;
    }
    if (status === 'ready' && lines.length === 0) {
      return <Text dimColor>(empty document)</Text>;
    }
    if (status !== 'ready') {
      return null;
    }
    if (mode === 'micro' || mode === 'minimal') {
      const line = window[0] ?? physicalRows[scroll] ?? physicalRows[0] ?? '';
      return (
        <Box width="100%" minWidth={0} overflow="hidden">
          <Text wrap="truncate">{line === '' ? ' ' : line}</Text>
        </Box>
      );
    }
    return window.map((line, index) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed slices.
      <Box key={start + index} width="100%" minWidth={0} overflow="hidden" flexShrink={0}>
        <Text wrap="truncate">{line === '' ? ' ' : line}</Text>
      </Box>
    ));
  })();

  return (
    <Box width={width} height={height} overflow="hidden">
      <Pane
        title={baseBorderTitle}
        focused={focused}
        titleExtra={
          gotoPending !== null ? <Text color={theme.warning}>{` g${gotoPending}`}</Text> : undefined
        }
        paddingLeft={0}
        paddingRight={0}
        flexGrow={1}
        {...(showScrollbar ? { scrollbar: { height: windowRows, thumb } } : {})}
        footerRight={<Text dimColor>{kind}</Text>}
      >
        {body === null ? (
          <Box flexGrow={1} minHeight={0} />
        ) : (
          <Box flexDirection="column" flexShrink={0} height={windowRows} overflow="hidden">
            {body}
          </Box>
        )}
      </Pane>
    </Box>
  );
});
