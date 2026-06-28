/**
 * StageDocPane — read-only document viewer (fixture-friendly pane contract).
 *
 * Accepts explicit allocated `width`/`height` (full pane size incl. border/title/padding) and
 * degrades via a local `layout(width, height)` router. Phase 0 renders from fixture lines;
 * live store wiring lands in a later phase.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { useTheme } from '../../theme/themeStore.js';
import { Pane } from '../Pane.js';
import { computeDocWindow, computeScrollThumb } from './docWindow.js';

/** Horizontal chrome: side borders only; the scrollbar is the right border, not content gutter. */
const CHROME_WIDTH = 2;
/** Vertical chrome: inline title row + bottom border. */
const CHROME_HEIGHT = 2;
/** Fixed top-border chrome: `╭─ ` + ` ╮`. */
const TITLE_CHROME_WIDTH = 5;
export type DocKind = 'plan' | 'note' | 'report';

export type StageDocDisplayMode = 'full' | 'compact' | 'minimal' | 'micro';

export interface StageDocPaneProps {
  /** Full pane allocation width (border box). */
  readonly width: number;
  /** Full pane allocation height (border box). */
  readonly height: number;
  readonly focused: boolean;
  readonly title: string;
  readonly lines: readonly string[];
  readonly scroll: number;
  readonly status?: 'ready' | 'loading' | 'error';
  readonly error?: string | null;
}

function contentInnerWidth(width: number): number {
  return Math.max(1, width - CHROME_WIDTH);
}

function contentInnerHeight(height: number): number {
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
 * Pane border title for the allocated width — name rule: names ≤6 chars show in full; longer names
 * keep ≥6 leading characters when truncated; at extreme narrow widths collapse to `…`.
 */
export function formatDocBorderTitle(
  name: string,
  width: number,
  mode: StageDocDisplayMode,
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
export function layout(width: number, height: number): StageDocDisplayMode {
  const innerW = contentInnerWidth(width);
  const innerH = contentInnerHeight(height);

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

/** Full width uses soft wrap; narrow modes keep one terminal row per logical line. */
function bodyWrapMode(mode: StageDocDisplayMode): 'wrap' | 'truncate' {
  return mode === 'full' ? 'wrap' : 'truncate';
}

function windowRowCount(innerH: number): number {
  return Math.max(0, innerH);
}

export const StageDocPane = memo(function StageDocPane({
  width,
  height,
  focused,
  title,
  lines,
  scroll,
  status = 'ready',
  error = null,
}: StageDocPaneProps): React.JSX.Element {
  const theme = useTheme();
  const mode = layout(width, height);
  const innerH = contentInnerHeight(height);
  const windowRows = windowRowCount(innerH);
  const { start, end } = computeDocWindow(lines.length, scroll, Math.max(windowRows, 1));
  const window = windowRows > 0 ? lines.slice(start, end) : [];
  const thumb = windowRows > 0 ? computeScrollThumb(lines.length, start, windowRows) : null;
  const basename = docBasename(title);
  const borderTitle = formatDocBorderTitle(basename, width, mode);
  const kind = docKindFromTitle(title);
  const showScrollbar = windowRows > 0 && mode !== 'micro';
  const wrap = bodyWrapMode(mode);

  const body = (() => {
    if (windowRows === 0) {
      return null;
    }
    if (status === 'error' && error !== null) {
      return <Text color={theme.error}>{`error: ${error}`}</Text>;
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
      const line = window[0] ?? lines[scroll] ?? lines[0] ?? '';
      return <Text wrap={wrap}>{line === '' ? ' ' : line}</Text>;
    }
    return window.map((line, index) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed slices.
      <Text key={start + index} wrap={wrap}>
        {line === '' ? ' ' : line}
      </Text>
    ));
  })();

  return (
    <Box width={width} height={height} overflow="hidden">
      <Pane
        title={borderTitle}
        focused={focused}
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
