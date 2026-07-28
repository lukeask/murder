import type { TerminalUpdate } from '../application/ApplicationClient.js';
import type {
  TerminalBuffer,
  TerminalCell,
  TerminalColor,
  TerminalKeyframe,
  TerminalRendition,
} from '../generated/applicationProtocol.js';
import type {
  TerminalColor as SurfaceColor,
  TerminalBufferInput,
  TerminalCellInput,
  TerminalKeyframeInput,
  TerminalRenditionInput,
  TerminalSurfaceUpdate,
} from './types.js';

function color(input: TerminalColor): SurfaceColor {
  if (input.kind === 'default') return undefined;
  if (input.kind === 'indexed') return input.index ?? undefined;
  return `rgb(${input.red ?? 0},${input.green ?? 0},${input.blue ?? 0})`;
}

function cell(input: TerminalCell): TerminalCellInput {
  const rendition: TerminalRendition = input.rendition;
  return {
    text: input.text,
    width: input.width,
    continuation: input.width === 0,
    fg: color(rendition.foreground),
    bg: color(rendition.background),
    bold: rendition.bold,
    dim: rendition.faint,
    italic: rendition.italic,
    underline: rendition.underline,
    inverse: rendition.inverse,
    hidden: rendition.invisible,
    strikethrough: rendition.strikethrough,
  };
}

function rendition(input: TerminalRendition): TerminalRenditionInput {
  return {
    fg: color(input.foreground),
    bg: color(input.background),
    bold: input.bold,
    dim: input.faint,
    italic: input.italic,
    underline: input.underline,
    inverse: input.inverse,
    hidden: input.invisible,
    strikethrough: input.strikethrough,
  };
}

function buffer(input: TerminalBuffer, columns: number, rows: number): TerminalBufferInput {
  return {
    cells: Array.from({ length: rows }, (_, row) =>
      input.cells.slice(row * columns, (row + 1) * columns).map(cell),
    ),
    cursor: {
      x: input.cursor.column,
      y: input.cursor.row,
      visible: input.cursor.visible,
      shape: input.cursor.shape,
    },
    saved_cursor: {
      x: input.saved_cursor.column,
      y: input.saved_cursor.row,
      visible: input.saved_cursor.visible,
      shape: input.saved_cursor.shape,
    },
    rendition: rendition(input.rendition),
    saved_rendition: rendition(input.saved_rendition),
    scroll_top: input.scroll_top,
    scroll_bottom: input.scroll_bottom,
  };
}

function keyframe(input: TerminalKeyframe): TerminalKeyframeInput {
  return {
    type: 'terminal.keyframe',
    sequence: input.sequence,
    columns: input.columns,
    rows: input.rows,
    primary: buffer(input.primary, input.columns, input.rows),
    alternate: buffer(input.alternate, input.columns, input.rows),
    active_buffer: input.active_buffer,
    rendition: rendition(input.rendition),
    modes: {
      applicationCursor: input.modes.application_cursor,
      applicationKeypad: input.modes.application_keypad,
      bracketedPaste: input.modes.bracketed_paste,
      autoWrap: input.modes.wraparound,
      origin: input.modes.origin,
      insert: input.modes.insert,
      alternate: input.active_buffer === 'alternate',
      synchronizedUpdates: input.modes.synchronized_updates,
    },
  };
}

/** Isolates generated wire-shape details from the reusable VT emulator. */
export function adaptTerminalUpdate(update: TerminalUpdate): TerminalSurfaceUpdate {
  if (update.type === 'terminal.keyframe') return keyframe(update);
  if (update.type === 'terminal.resynced') return keyframe(update.keyframe);
  if (update.type === 'terminal.chunk') return update;
  if (update.type === 'terminal.frame') return update;
  return {
    type: 'terminal.gap',
    expected_sequence: update.expected_sequence,
    next_sequence: update.next_sequence,
  };
}
