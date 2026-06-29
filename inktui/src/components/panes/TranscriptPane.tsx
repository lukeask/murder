/**
 * TranscriptPane — explicit width/height pane contract for crow chat history.
 *
 * Store-free: callers pass display-ready turns plus explicit chrome and dimensions. The layout
 * router keeps the pane deterministic across the layout manager's allocated sizes.
 */

import { Box, Text } from 'ink';
import { memo, useEffect, useMemo } from 'react';
import type { ChatTurn, TurnSpeaker } from '../../selectors/conversationsSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { Pane } from '../Pane.js';
import { type ChatLine, flattenTurns } from './chatLines.js';
import { computeScrollThumb, computeTranscriptWindow } from './shared/scrollWindow.js';

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

export type ChatDisplayMode = 'full' | 'compact' | 'minimal' | 'tiny';

export interface TranscriptPaneTurn {
  readonly speaker: TurnSpeaker;
  readonly lines: readonly string[];
  readonly tone?: ChatLine['tone'];
}

export interface TranscriptPaneProps {
  /** Full pane allocation including border, title, and footer. */
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
  readonly title: string;
  readonly footerLeft: string;
  readonly footerRight: string;
  readonly turns: readonly ChatTurn[];
  readonly viewMode: 'verbose' | 'condensed' | 'tmux';
  readonly scrollUp: number;
  readonly gotoLine: number | null;
  readonly onScrollUpChange?: (scrollUp: number) => void;
  readonly onWindowMetricsChange?: (metrics: {
    readonly lineCount: number;
    readonly maxScrollUp: number;
  }) => void;
  readonly tmuxFrame?: string;
  readonly tmuxWaitingText?: string;
  readonly titleExtra?: React.ReactNode;
}

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

function TmuxFrameBody({
  frame,
  height,
  waitingText,
}: {
  readonly frame: string | undefined;
  readonly height: number;
  readonly waitingText: string;
}): React.JSX.Element {
  const lines = (frame !== undefined && frame !== '' ? frame : waitingText)
    .split('\n')
    .slice(0, Math.max(height, 0));
  const keyedLines = lines.map((text, index) => ({
    key: `tmux-${index}:${text}`,
    text,
  }));

  return (
    <Box
      flexDirection="column"
      flexShrink={0}
      width="100%"
      minWidth={0}
      height={height}
      overflow="hidden"
    >
      {keyedLines.map(({ key, text }) => (
        <Box key={key} flexShrink={0}>
          <Text wrap="truncate">{text === '' ? ' ' : text}</Text>
        </Box>
      ))}
    </Box>
  );
}

export const TranscriptPane = memo(function TranscriptPane({
  width,
  height,
  focused,
  title,
  footerLeft,
  footerRight,
  turns,
  viewMode,
  scrollUp,
  gotoLine,
  onScrollUpChange,
  onWindowMetricsChange,
  tmuxFrame,
  tmuxWaitingText = '[waiting for tmux frame…]',
  titleExtra,
}: TranscriptPaneProps): React.JSX.Element {
  const theme = useTheme();
  const displayMode = layout(width, height);
  const innerH = contentHeight(height);
  const lines = useMemo(() => (viewMode === 'tmux' ? [] : flattenTurns(turns)), [turns, viewMode]);
  const window = computeTranscriptWindow(lines.length, scrollUp, innerH, gotoLine);
  const visibleLines = lines.slice(window.start, window.end);
  const keyedVisibleLines = visibleLines.map((line, index) => ({
    key: `${window.start + index}:${line.speaker}:${line.kind}:${line.text}`,
    line,
  }));

  useEffect(() => {
    if (gotoLine !== null && onScrollUpChange !== undefined) {
      onScrollUpChange(window.clampedScrollUp);
    }
  }, [gotoLine, onScrollUpChange, window.clampedScrollUp]);

  useEffect(() => {
    onWindowMetricsChange?.({ lineCount: lines.length, maxScrollUp: window.maxScrollUp });
  }, [lines.length, onWindowMetricsChange, window.maxScrollUp]);

  const guttersVisible = showGutters(displayMode);
  const thumb = computeScrollThumb(lines.length, window.start, innerH);
  const footers = footerLevel(width);
  const footerLeftText = formatFooterLeft(footerLeft, footers);
  const showFooterChrome = footerLeftText !== null && footerVisible(height, footers);
  const scrollbarVisible = showScrollbar(displayMode, innerH, lines.length, innerH);

  const body = (() => {
    if (viewMode === 'tmux') {
      return <TmuxFrameBody frame={tmuxFrame} height={innerH} waitingText={tmuxWaitingText} />;
    }
    if (visibleLines.length === 0) {
      return <Text dimColor>no history</Text>;
    }
    if (displayMode === 'tiny') {
      const lastContent = [...visibleLines].reverse().find((line) => line.kind !== 'blank');
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
    return keyedVisibleLines.map(({ key, line }) => (
      <ChatHistoryLine key={key} line={line} theme={theme} showGutter={guttersVisible} />
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
