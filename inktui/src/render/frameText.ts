/**
 * Text-frame utilities for workspace slide animation (workspaces plan, step 4a).
 *
 * Frames are newline-separated terminal rows with optional ANSI CSI/SGR sequences. Row width is
 * measured in **display columns** (CSI stripped, one column per UTF-16 code point — same rule as
 * {@link ../../test/fixtures/pane_rendering/renderInkFixture.tsx} fixture snapshots).
 */

// biome-ignore lint/suspicious/noControlCharactersInRegex: width checks must ignore all CSI escapes.
const ANSI_CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;

/** A fixed-size captured terminal frame (same shape as {@link ../input/workspaceStore.js CapturedFrame}). */
export interface TextFrame {
  readonly text: string;
  readonly columns: number;
  readonly rows: number;
}

/** Which way a workspace switch travels — determines vertical concat order for the slide surface. */
export type FrameConcatDirection = 'next' | 'prev';

export function stripAnsiCsi(text: string): string {
  return text.replace(ANSI_CSI_RE, '');
}

export function displayWidth(text: string): number {
  return Array.from(stripAnsiCsi(text)).length;
}

function splitFrameLines(text: string): string[] {
  return text.length === 0 ? [] : text.split('\n');
}

function padRowToColumns(line: string, columns: number): string {
  const width = displayWidth(line);
  if (width > columns) {
    throw new Error(`frame row is ${width} columns wide, exceeding frame width ${columns}`);
  }
  return line + ' '.repeat(columns - width);
}

/** Normalize `frame` to exactly `rows` lines, each padded to `columns` display width. */
export function normalizeTextFrame(frame: TextFrame): TextFrame {
  const { columns, rows } = frame;
  const lines = splitFrameLines(frame.text);
  if (lines.length > rows) {
    throw new Error(`frame has ${lines.length} rows, exceeding declared height ${rows}`);
  }
  const padded = lines.map((line) => padRowToColumns(line, columns));
  while (padded.length < rows) {
    padded.push(' '.repeat(columns));
  }
  return { text: padded.join('\n'), columns, rows };
}

/**
 * Vertically concatenate two same-sized frames for slide compositing.
 *
 * `next` (J): `from` on top, `to` below — the window offset increases toward `to`.
 * `prev` (K): `to` on top, `from` below — the window offset decreases from `from` toward `to`.
 */
export function concatFrames(
  from: TextFrame,
  to: TextFrame,
  direction: FrameConcatDirection,
): TextFrame {
  if (from.columns !== to.columns || from.rows !== to.rows) {
    throw new Error(
      `frame size mismatch: from is ${from.columns}x${from.rows}, to is ${to.columns}x${to.rows}`,
    );
  }
  const top = direction === 'next' ? from : to;
  const bottom = direction === 'next' ? to : from;
  const normalizedTop = normalizeTextFrame(top);
  const normalizedBottom = normalizeTextFrame(bottom);
  return {
    text: `${normalizedTop.text}\n${normalizedBottom.text}`,
    columns: from.columns,
    rows: from.rows + to.rows,
  };
}

/**
 * Slice a `rows`-tall window out of a (usually concatenated) frame starting at `offsetRows`.
 * Out-of-range offsets clamp: missing lines are blank rows padded to `frame.columns`.
 */
export function sliceFrameWindow(frame: TextFrame, offsetRows: number, rows: number): TextFrame {
  const normalized = normalizeTextFrame(frame);
  const lines = splitFrameLines(normalized.text);
  const start = Math.max(0, Math.floor(offsetRows));
  const window: string[] = [];
  for (let i = 0; i < rows; i += 1) {
    const line = lines[start + i];
    window.push(line === undefined ? ' '.repeat(frame.columns) : line);
  }
  return { text: window.join('\n'), columns: frame.columns, rows };
}
