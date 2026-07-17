/**
 * DocumentSurface — store-free read-only document pane.
 *
 * Accepts explicit allocated `width`/`height` and display-ready physical rows. Controllers own
 * source parsing/layout, store reads, keymaps, and input; this surface owns the visible window and
 * Pane chrome.
 *
 * Every body row already occupies exactly one terminal row. Never combine logical-line windowing
 * with `Text wrap="wrap"`.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import type { StyledDocumentRow } from '../../render/documentLayout.js';
import { useTheme } from '../../theme/themeStore.js';
import { terminalSafeText } from '../../utils/terminalSafeText.js';
import { Pane } from '../Pane.js';
import { computeDocumentWindow, computeScrollThumb } from './shared/scrollWindow.js';

/** Horizontal chrome: side borders only; the scrollbar is the right border, not content gutter. */
const CHROME_WIDTH = 2;
/** Vertical chrome: inline title row + bottom border. */
const CHROME_HEIGHT = 2;
/** Fixed top-border chrome: `╭─ ` + ` ╮`. */
const TITLE_CHROME_WIDTH = 5;
export type DocKind = 'plan' | 'note' | 'report';

export type DocumentSizeMode = 'full' | 'compact' | 'minimal' | 'micro';

export interface DocumentSurfaceProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly title: string;
  readonly rows: readonly StyledDocumentRow[];
  readonly scroll: number;
  readonly gotoPending?: string | null;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

export function documentContentInnerWidth(width: number): number {
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
export function formatDocBorderTitle(name: string, width: number, mode: DocumentSizeMode): string {
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
export function layout(width: number, height: number): DocumentSizeMode {
  const innerW = documentContentInnerWidth(width);
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

function windowRowCount(innerH: number): number {
  return Math.max(0, innerH);
}

export const DocumentSurface = memo(function DocumentSurface({
  width,
  height,
  focused,
  title,
  rows,
  scroll,
  gotoPending = null,
  status = 'ready',
  error = null,
}: DocumentSurfaceProps): React.JSX.Element {
  const theme = useTheme();
  const mode = layout(width, height);
  const innerH = documentContentInnerHeight(height);
  const windowRows = windowRowCount(innerH);
  const { start, end } = computeDocumentWindow(rows.length, scroll, Math.max(windowRows, 1));
  const window = windowRows > 0 ? rows.slice(start, end) : [];
  const thumb = windowRows > 0 ? computeScrollThumb(rows.length, start, windowRows) : null;
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
    if (status === 'ready' && rows.length === 0) {
      return <Text dimColor>(empty document)</Text>;
    }
    if (status !== 'ready') {
      return null;
    }
    if (mode === 'micro' || mode === 'minimal') {
      const displayRow = window[0] ?? rows[scroll] ?? rows[0];
      return (
        <Box width="100%" minWidth={0} overflow="hidden">
          <StyledRow row={displayRow} />
        </Box>
      );
    }
    return window.map((displayRow, index) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed slices.
      <Box key={start + index} width="100%" minWidth={0} overflow="hidden" flexShrink={0}>
        <StyledRow row={displayRow} />
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

function StyledRow({
  row: displayRow,
}: {
  readonly row: StyledDocumentRow | undefined;
}): React.JSX.Element {
  if (displayRow === undefined || displayRow.runs.length === 0) {
    return <Text wrap="truncate"> </Text>;
  }
  return (
    <Text wrap="truncate">
      {displayRow.runs.map((run, index) => (
        <Text
          // biome-ignore lint/suspicious/noArrayIndexKey: runs are immutable positional layout data.
          key={index}
          {...(run.style.fg !== undefined ? { color: run.style.fg } : {})}
          {...(run.style.bg !== undefined ? { backgroundColor: run.style.bg } : {})}
          {...(run.style.bold !== undefined ? { bold: run.style.bold } : {})}
          {...(run.style.dim !== undefined ? { dimColor: run.style.dim } : {})}
          {...(run.style.italic !== undefined ? { italic: run.style.italic } : {})}
          {...(run.style.underline !== undefined ? { underline: run.style.underline } : {})}
          {...(run.style.strikethrough !== undefined
            ? { strikethrough: run.style.strikethrough }
            : {})}
        >
          {run.text}
        </Text>
      ))}
    </Text>
  );
}
