/**
 * ChatPane — explicit width/height pane contract for crow chat history.
 *
 * Store-free: callers pass display-ready turns and chrome props. Matches the old
 * {@link ../Stage.tsx ChatPane} gutter + scroll window at large sizes; smaller allocations
 * route through {@link layout} into deterministic display modes.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import type { ChatTurn, TurnSpeaker } from '../../selectors/conversationsSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { computeScrollThumb } from '../DocPane.js';
import { Pane } from '../Pane.js';
import { flattenTurns } from '../Stage.js';

const GUTTER_HEAD = '▌';
const GUTTER_CONT = '▏';

/** Title row plus bottom border row reserved outside the content budget. */
const CHROME_ROWS = 2;
/** Side border columns (left + right). */
const BORDER_COLS = 2;
/** Separator between harness and model in the display-ready `footerLeft` string. */
const FOOTER_SEP = '◇';
/** Inner width for full `harness ◇ model` + worktree on the bottom border. */
const MIN_FOOTER_FULL_INNER_W = 28;
/** Inner width for model-only + worktree (harness dropped first). */
const MIN_FOOTER_MODEL_INNER_W = 14;
/** Scrollbar steals a column; hide it when the body is too short to benefit. */
const MIN_SCROLLBAR_INNER_H = 6;
/** Drop the bottom-bar overlay when vertical budget cannot fit title + one content row. */
const MIN_FOOTER_INNER_H = 4;
/** Gutter column width when speaker rails are shown. */
const GUTTER_COLS = 2;

export type ChatDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export interface ChatPaneTurn {
  readonly speaker: 'user' | 'assistant' | 'tool';
  readonly lines: readonly string[];
}

export interface ChatPaneProps {
  /** Full pane allocation including border, title, and footer. */
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly title: string;
  readonly footerLeft: string;
  readonly footerRight: string;
  readonly turns: readonly ChatPaneTurn[];
  readonly titleExtra?: React.ReactNode;
}

type ChatLine = ReturnType<typeof flattenTurns>[number];

function contentHeight(height: number): number {
  return Math.max(1, height - CHROME_ROWS);
}

function contentWidth(width: number): number {
  return Math.max(1, width - BORDER_COLS);
}

/**
 * Deterministic size router — centralizes what the pane shows at each allocation.
 * Height thresholds pair with footer/scrollbar gating in the render path (Phase 2).
 */
export function layout(width: number, height: number): ChatDisplayMode {
  const innerH = contentHeight(height);
  const innerW = contentWidth(width);
  if (innerH <= 3 || innerW < 12) {
    return 'tiny';
  }
  if (innerH < 5 || innerW < 18) {
    return 'minimal';
  }
  if (innerH < 7 || innerW < 24) {
    return 'compact';
  }
  return 'full';
}

function fixtureTurnsToChatTurns(turns: readonly ChatPaneTurn[]): readonly ChatTurn[] {
  return turns.map((turn, index) => ({
    speaker: turn.speaker,
    text: turn.lines.join('\n'),
    blockId: `fixture-turn-${index}`,
  }));
}

function speakerColor(speaker: TurnSpeaker, theme: ReturnType<typeof useTheme>): string {
  switch (speaker) {
    case 'user':
      return theme.success;
    case 'assistant':
      return theme.text;
    case 'tool':
      return theme.warning;
    case 'plan':
      return theme.heading;
    case 'notice':
      return theme.error;
    default:
      return theme.muted;
  }
}

function chatLineColor(line: ChatLine, theme: ReturnType<typeof useTheme>): string {
  if (line.tone === 'summary') {
    return theme.accent;
  }
  return speakerColor(line.speaker, theme);
}

function ChatHistoryLine({
  line,
  theme,
  showGutter,
}: {
  readonly line: ChatLine;
  readonly theme: ReturnType<typeof useTheme>;
  readonly showGutter: boolean;
}): React.JSX.Element {
  const gutterColor = chatLineColor(line, theme);
  const verbatim = line.kind === 'code' || line.kind === 'pre';
  const content =
    line.kind === 'blank' ? (
      <Text> </Text>
    ) : verbatim ? (
      <Box flexGrow={1} minWidth={0} flexDirection="column">
        <Text dimColor wrap="wrap">
          {line.text === '' ? ' ' : line.text}
        </Text>
      </Box>
    ) : (
      <Text color={gutterColor} wrap="wrap">
        {line.text === '' ? ' ' : line.text}
      </Text>
    );

  return (
    <Box flexDirection="row" flexShrink={0}>
      {showGutter ? (
        <Box flexShrink={0} width={2}>
          {line.kind === 'blank' && line.gutter === 'none' ? (
            <Text> </Text>
          ) : (
            <Text color={gutterColor}>{line.firstOfTurn ? GUTTER_HEAD : GUTTER_CONT} </Text>
          )}
        </Box>
      ) : null}
      <Box flexGrow={1} minWidth={0} flexDirection="column">
        {content}
      </Box>
    </Box>
  );
}

type FooterLevel = 'full' | 'model' | 'none';

function parseHarnessModelFooter(footerLeft: string): {
  readonly harness: string | null;
  readonly model: string | null;
} {
  const sepIdx = footerLeft.indexOf(FOOTER_SEP);
  if (sepIdx === -1) {
    const trimmed = footerLeft.trim();
    return { harness: null, model: trimmed === '' ? null : trimmed };
  }
  const harness = footerLeft.slice(0, sepIdx).trim();
  const model = footerLeft.slice(sepIdx + FOOTER_SEP.length).trim();
  return {
    harness: harness === '' ? null : harness,
    model: model === '' ? null : model,
  };
}

/** Progressive footer: full harness ◇ model, then model-only, then omit the bottom bar. */
function footerLevel(width: number): FooterLevel {
  const innerW = contentWidth(width);
  if (innerW >= MIN_FOOTER_FULL_INNER_W) {
    return 'full';
  }
  if (innerW >= MIN_FOOTER_MODEL_INNER_W) {
    return 'model';
  }
  return 'none';
}

function formatFooterLeft(footerLeft: string, level: FooterLevel): string | null {
  if (level === 'none') {
    return null;
  }
  const { harness, model } = parseHarnessModelFooter(footerLeft);
  if (level === 'model') {
    return model ?? harness ?? footerLeft;
  }
  if (harness !== null && model !== null) {
    return `${harness} ${FOOTER_SEP} ${model}`;
  }
  return harness ?? model ?? footerLeft;
}

function showGutters(mode: ChatDisplayMode): boolean {
  return mode === 'full' || mode === 'compact';
}

function showScrollbar(
  mode: ChatDisplayMode,
  innerH: number,
  lineCount: number,
  windowRows: number,
): boolean {
  if (mode === 'tiny' || innerH < MIN_SCROLLBAR_INNER_H) {
    return false;
  }
  return lineCount > windowRows;
}

function footerVisible(height: number, level: FooterLevel): boolean {
  if (level === 'none') {
    return false;
  }
  return contentHeight(height) >= MIN_FOOTER_INNER_H;
}

function bodyTextCols(innerW: number, guttersVisible: boolean): number {
  return Math.max(1, innerW - (guttersVisible ? GUTTER_COLS : 0));
}

/** Upper-bound terminal rows for a soft-wrapped logical line (deterministic, no measure). */
function estimateWrapRows(text: string, cols: number): number {
  if (text === '') {
    return 1;
  }
  return Math.max(1, Math.ceil(text.length / cols));
}

function lineVisualRows(line: ChatLine, cols: number): number {
  if (line.kind === 'blank') {
    return 1;
  }
  return estimateWrapRows(line.text, cols);
}

/**
 * Tail-pinned slice for the scroll window. Walks backward from the newest line,
 * accumulating estimated wrap rows until the inner-height budget is met. A single
 * long line may still clip at the bottom when it exceeds the budget — preferable
 * to pushing title chrome off-screen (long-fixture Phase 3).
 */
function computeChatWindow(
  lines: readonly ChatLine[],
  innerH: number,
  textCols: number,
): { readonly start: number; readonly end: number } {
  const end = lines.length;
  if (end === 0 || innerH <= 0) {
    return { start: 0, end: 0 };
  }
  let used = 0;
  let start = end;
  for (let i = end - 1; i >= 0; i--) {
    const line = lines[i];
    if (line === undefined) {
      continue;
    }
    const rows = lineVisualRows(line, textCols);
    if (used + rows > innerH && start < end) {
      break;
    }
    used += rows;
    start = i;
    if (used >= innerH) {
      break;
    }
  }
  return { start, end };
}

export const ChatPane = memo(function ChatPane({
  width,
  height,
  focused,
  title,
  footerLeft,
  footerRight,
  turns,
  titleExtra,
}: ChatPaneProps): React.JSX.Element {
  const theme = useTheme();
  const displayMode = layout(width, height);
  const innerH = contentHeight(height);
  const lines = useMemo(() => flattenTurns(fixtureTurnsToChatTurns(turns)), [turns]);

  const guttersVisible = showGutters(displayMode);
  const textCols = bodyTextCols(contentWidth(width), guttersVisible);
  const { start, end } =
    displayMode === 'tiny'
      ? { start: 0, end: lines.length }
      : computeChatWindow(lines, innerH, textCols);
  const visibleLines = displayMode === 'tiny' ? lines : lines.slice(start, end);
  const thumb = computeScrollThumb(lines.length, start, innerH);
  const footers = footerLevel(width);
  const footerLeftText = formatFooterLeft(footerLeft, footers);
  const showFooterChrome = footerLeftText !== null && footerVisible(height, footers);
  const scrollbarVisible = showScrollbar(displayMode, innerH, lines.length, innerH);

  const body = (() => {
    if (lines.length === 0) {
      return <Text dimColor>no history</Text>;
    }
    if (displayMode === 'tiny') {
      const lastContent = [...lines].reverse().find((line) => line.kind !== 'blank');
      if (lastContent === undefined) {
        return <Text dimColor>no history</Text>;
      }
      const wrap = innerH <= 2 ? 'truncate' : 'wrap';
      return (
        <Text wrap={wrap} color={chatLineColor(lastContent, theme)}>
          {lastContent.text}
        </Text>
      );
    }
    return visibleLines.map((line) => (
      <ChatHistoryLine
        key={`${line.speaker}:${line.kind}:${line.text}`}
        line={line}
        theme={theme}
        showGutter={guttersVisible}
      />
    ));
  })();

  return (
    <Box width={width} height={height} overflow="hidden">
      <Pane
        title={title}
        focused={focused}
        titleExtra={titleExtra}
        flexGrow={1}
        paddingLeft={0}
        paddingRight={0}
        {...(scrollbarVisible ? { scrollbar: { height: innerH, thumb } } : {})}
        footerLeft={showFooterChrome ? <Text dimColor>{footerLeftText}</Text> : undefined}
        footerRight={showFooterChrome ? <Text dimColor>{footerRight}</Text> : undefined}
      >
        <Box flexDirection="column" flexShrink={0} height={innerH} overflow="hidden">
          {body}
        </Box>
      </Pane>
    </Box>
  );
});
