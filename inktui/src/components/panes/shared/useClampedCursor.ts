import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useState } from 'react';

export interface ClampedCursor {
  readonly cursor: number;
  readonly setCursor: Dispatch<SetStateAction<number>>;
  readonly moveDown: () => void;
  readonly moveUp: () => void;
}

function clampCursor(cursor: number, rowCount: number): number {
  return Math.min(Math.max(cursor, 0), Math.max(rowCount - 1, 0));
}

export function useClampedCursor(rowCount: number): ClampedCursor {
  const [cursorState, setCursor] = useState(0);
  const cursor = clampCursor(cursorState, rowCount);

  const moveDown = useCallback(() => {
    setCursor((current) => clampCursor(clampCursor(current, rowCount) + 1, rowCount));
  }, [rowCount]);

  const moveUp = useCallback(() => {
    setCursor((current) => clampCursor(clampCursor(current, rowCount) - 1, rowCount));
  }, [rowCount]);

  return { cursor, setCursor, moveDown, moveUp };
}
