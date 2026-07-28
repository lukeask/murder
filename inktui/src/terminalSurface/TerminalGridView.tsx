import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import type { TerminalCell, TerminalColor, TerminalGridSnapshot } from './types.js';

const ANSI_COLORS = [
  'black',
  'red',
  'green',
  'yellow',
  'blue',
  'magenta',
  'cyan',
  'white',
] as const;
const BRIGHT_ANSI_COLORS = [
  'gray',
  'redBright',
  'greenBright',
  'yellowBright',
  'blueBright',
  'magentaBright',
  'cyanBright',
  'whiteBright',
] as const;

function inkColor(color: TerminalColor): string | undefined {
  if (typeof color === 'string') return color.startsWith('ansi256:') ? undefined : color;
  if (color === undefined) return undefined;
  return color < 8 ? ANSI_COLORS[color] : BRIGHT_ANSI_COLORS[color - 8];
}

interface Segment {
  readonly key: string;
  readonly text: string;
  readonly cell: TerminalCell;
  readonly cursor: boolean;
}

function sameStyle(left: Segment, right: Segment): boolean {
  const a = left.cell;
  const b = right.cell;
  return (
    left.cursor === right.cursor &&
    a.fg === b.fg &&
    a.bg === b.bg &&
    a.bold === b.bold &&
    a.dim === b.dim &&
    a.italic === b.italic &&
    a.underline === b.underline &&
    a.inverse === b.inverse &&
    a.hidden === b.hidden &&
    a.strikethrough === b.strikethrough
  );
}

function rowSegments(
  cells: readonly TerminalCell[],
  cursorX: number,
  cursorY: number,
  cursorVisible: boolean,
  y: number,
  columns: number,
  offsetColumn: number,
): readonly Segment[] {
  const result: Segment[] = [];
  for (let x = offsetColumn; x < offsetColumn + columns; x += 1) {
    const cell = cells[x];
    if (cell === undefined) continue;
    if (cell.continuation) {
      // When a horizontal viewport begins on the second half of a wide cell, retain that physical
      // column as a blank rather than shifting every following cell one column left.
      if (x === offsetColumn) {
        result.push({ key: `${x}`, text: ' ', cell: { ...cell, width: 1 }, cursor: false });
      }
      continue;
    }
    const cursor = cursorVisible && cursorY === y && cursorX === x;
    const text = cell.hidden ? ' '.repeat(cell.width) : cell.text || ' ';
    const next: Segment = { key: `${x}`, text, cell, cursor };
    const prior = result.at(-1);
    if (prior !== undefined && sameStyle(prior, next)) {
      result[result.length - 1] = { ...prior, text: `${prior.text}${text}` };
    } else result.push(next);
  }
  return result;
}

interface GridRowProps {
  readonly cells: readonly TerminalCell[];
  readonly y: number;
  readonly columns: number;
  readonly rowVersion: number;
  readonly cursorX: number;
  readonly cursorY: number;
  readonly cursorVisible: boolean;
  readonly offsetColumn: number;
}

const GridRow = memo(
  function GridRow({
    cells,
    y,
    columns,
    cursorX,
    cursorY,
    cursorVisible,
    offsetColumn,
  }: GridRowProps): React.JSX.Element {
    const segments = useMemo(
      () => rowSegments(cells, cursorX, cursorY, cursorVisible, y, columns, offsetColumn),
      [cells, columns, cursorVisible, cursorX, cursorY, offsetColumn, y],
    );
    return (
      <Box flexShrink={0} width={columns} height={1} overflow="hidden">
        {segments.map((segment) => {
          const cell = segment.cell;
          const foreground = inkColor(cell.fg);
          const background = inkColor(cell.bg);
          return (
            <Text
              key={segment.key}
              {...(foreground === undefined ? {} : { color: foreground })}
              {...(background === undefined ? {} : { backgroundColor: background })}
              bold={cell.bold}
              dimColor={cell.dim}
              italic={cell.italic}
              underline={cell.underline}
              strikethrough={cell.strikethrough}
              inverse={cell.inverse !== segment.cursor}
              wrap="truncate"
            >
              {segment.text}
            </Text>
          );
        })}
      </Box>
    );
  },
  (previous, next) => {
    if (
      previous.cells !== next.cells ||
      previous.columns !== next.columns ||
      previous.rowVersion !== next.rowVersion ||
      previous.offsetColumn !== next.offsetColumn
    )
      return false;
    const cursorTouched =
      (previous.cursorVisible && previous.cursorY === previous.y) ||
      (next.cursorVisible && next.cursorY === next.y);
    return !cursorTouched;
  },
);

/** Pure Ink view for a cell grid. The cursor is synthetic (inverse video), never a real terminal cursor. */
export const TerminalGridView = memo(function TerminalGridView({
  snapshot,
  width,
  height,
  offsetColumn = 0,
  offsetRow = 0,
}: {
  readonly snapshot: TerminalGridSnapshot;
  readonly width: number;
  readonly height: number;
  readonly offsetColumn?: number;
  readonly offsetRow?: number;
}): React.JSX.Element {
  const clampedOffsetColumn = Math.max(
    0,
    Math.min(Math.floor(offsetColumn), Math.max(0, snapshot.columns - 1)),
  );
  const clampedOffsetRow = Math.max(
    0,
    Math.min(Math.floor(offsetRow), Math.max(0, snapshot.rows - 1)),
  );
  const columns = Math.max(1, Math.min(width, snapshot.columns - clampedOffsetColumn));
  const rows = Math.max(1, Math.min(height, snapshot.rows - clampedOffsetRow));
  return (
    <Box flexDirection="column" flexShrink={0} width={columns} height={rows} overflow="hidden">
      {Array.from({ length: rows }, (_, viewportRow) => {
        const terminalRow = clampedOffsetRow + viewportRow;
        return (
          <GridRow
            // biome-ignore lint/suspicious/noArrayIndexKey: physical terminal row coordinates are stable identities.
            key={terminalRow}
            cells={snapshot.cells[terminalRow] ?? []}
            y={terminalRow}
            columns={columns}
            rowVersion={snapshot.rowVersions[terminalRow] ?? 0}
            cursorX={snapshot.cursor.x}
            cursorY={snapshot.cursor.y}
            cursorVisible={snapshot.cursor.visible}
            offsetColumn={clampedOffsetColumn}
          />
        );
      })}
    </Box>
  );
});
