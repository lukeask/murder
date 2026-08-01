import { Text } from 'ink';
import { memo, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';
import { TerminalGridView } from './TerminalGridView.js';
import { TerminalSurfaceStore } from '@murder/ui-core/terminalSurface/TerminalSurfaceStore.js';
import type {
  TerminalSizingPolicy,
  TerminalSurfaceUpdate,
  TerminalViewportCommand,
  TerminalViewportMetrics,
} from '@murder/ui-core/terminalSurface/types.js';

export interface TerminalSurfaceControllerProps {
  readonly update: TerminalSurfaceUpdate | null;
  readonly width: number;
  readonly height: number;
  readonly waitingText: string;
  readonly sizingPolicy: TerminalSizingPolicy;
  readonly viewportCommand?: TerminalViewportCommand | null;
  readonly onViewportChange?: (metrics: TerminalViewportMetrics) => void;
}

interface ViewportState {
  readonly offsetColumn: number;
  readonly offsetRow: number;
  readonly followingCursor: boolean;
}

function clampOffset(offset: number, terminalSize: number, viewportSize: number): number {
  return Math.max(0, Math.min(offset, Math.max(0, terminalSize - viewportSize)));
}

function followAxis(offset: number, cursor: number, viewportSize: number): number {
  if (cursor < offset) return cursor;
  if (cursor >= offset + viewportSize) return cursor - viewportSize + 1;
  return offset;
}

/** Binds stream ingestion to a persistent emulator and leaves rendering to TerminalGridView. */
export const TerminalSurfaceController = memo(function TerminalSurfaceController({
  update,
  width,
  height,
  waitingText,
  sizingPolicy,
  viewportCommand,
  onViewportChange,
}: TerminalSurfaceControllerProps): React.JSX.Element {
  const theme = useTheme();
  const storeRef = useRef<TerminalSurfaceStore | null>(null);
  if (storeRef.current === null) storeRef.current = new TerminalSurfaceStore();
  const store = storeRef.current;
  const snapshot = useSyncExternalStore(
    store.subscribe.bind(store),
    store.getSnapshot,
    store.getSnapshot,
  );
  const viewportColumns = Math.max(1, Math.min(Math.floor(width), snapshot.columns));
  const viewportRows = Math.max(1, Math.min(Math.floor(height), snapshot.rows));
  const [viewport, setViewport] = useState<ViewportState>({
    offsetColumn: 0,
    offsetRow: 0,
    followingCursor: true,
  });
  const lastViewportCommand = useRef(0);
  useEffect(() => {
    if (update !== null) store.ingest(update);
  }, [store, update]);
  useEffect(() => {
    setViewport((current) => {
      const following = current.followingCursor;
      const nextColumn = following
        ? followAxis(current.offsetColumn, snapshot.cursor.x, viewportColumns)
        : current.offsetColumn;
      const nextRow = following
        ? followAxis(current.offsetRow, snapshot.cursor.y, viewportRows)
        : current.offsetRow;
      const clampedColumn = clampOffset(nextColumn, snapshot.columns, viewportColumns);
      const clampedRow = clampOffset(nextRow, snapshot.rows, viewportRows);
      return clampedColumn === current.offsetColumn && clampedRow === current.offsetRow
        ? current
        : { ...current, offsetColumn: clampedColumn, offsetRow: clampedRow };
    });
  }, [
    snapshot.columns,
    snapshot.cursor.x,
    snapshot.cursor.y,
    snapshot.rows,
    viewportColumns,
    viewportRows,
  ]);
  useEffect(() => {
    if (
      viewportCommand === null ||
      viewportCommand === undefined ||
      viewportCommand.sequence <= lastViewportCommand.current
    ) {
      return;
    }
    lastViewportCommand.current = viewportCommand.sequence;
    setViewport((current) => {
      if (viewportCommand.kind === 'follow_cursor') {
        return {
          offsetColumn: clampOffset(
            followAxis(current.offsetColumn, snapshot.cursor.x, viewportColumns),
            snapshot.columns,
            viewportColumns,
          ),
          offsetRow: clampOffset(
            followAxis(current.offsetRow, snapshot.cursor.y, viewportRows),
            snapshot.rows,
            viewportRows,
          ),
          followingCursor: true,
        };
      }
      return {
        offsetColumn: clampOffset(
          current.offsetColumn + viewportCommand.deltaColumns,
          snapshot.columns,
          viewportColumns,
        ),
        offsetRow: clampOffset(
          current.offsetRow + viewportCommand.deltaRows,
          snapshot.rows,
          viewportRows,
        ),
        followingCursor: false,
      };
    });
  }, [snapshot, viewportColumns, viewportCommand, viewportRows]);
  const metrics = useMemo<TerminalViewportMetrics>(
    () => ({
      sizingPolicy,
      geometryMatchesPolicy:
        sizingPolicy.kind === 'follow_viewport' ||
        (snapshot.columns === sizingPolicy.columns && snapshot.rows === sizingPolicy.rows),
      terminalColumns: snapshot.columns,
      terminalRows: snapshot.rows,
      viewportColumns,
      viewportRows,
      offsetColumn: viewport.offsetColumn,
      offsetRow: viewport.offsetRow,
      followingCursor: viewport.followingCursor,
      cropped: viewportColumns < snapshot.columns || viewportRows < snapshot.rows,
    }),
    [sizingPolicy, snapshot.columns, snapshot.rows, viewport, viewportColumns, viewportRows],
  );
  useEffect(() => {
    onViewportChange?.(metrics);
  }, [metrics, onViewportChange]);
  if (update === null) return <Text color={theme.muted}>{waitingText}</Text>;
  return (
    <TerminalGridView
      snapshot={snapshot}
      width={viewportColumns}
      height={viewportRows}
      offsetColumn={viewport.offsetColumn}
      offsetRow={viewport.offsetRow}
    />
  );
});
