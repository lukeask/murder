/**
 * Shared helpers for panel lists that prepend a synthetic “+ create new” row at index 0.
 *
 * Creation RPCs/modals stay in App handlers; panels only inject the row and route Enter.
 */

/** True when the cursor sits on the synthetic create row (always index 0). */
export function isCreateCursor(cursor: number): boolean {
  return cursor === 0;
}

/** Map a list cursor (including create at 0) to a data-row index, or null on the create row. */
export function dataIndexFromCursor(cursor: number): number | null {
  if (cursor <= 0) {
    return null;
  }
  return cursor - 1;
}

/** Cursor row count when a create row is prepended to `dataCount` items. */
export function listRowCountWithCreate(dataCount: number): number {
  return dataCount + 1;
}

/**
 * Run `onCreate` when the cursor is on the create row; otherwise run `onOpen` with the data index.
 * Shared Enter / click routing for doc panels and workflows.
 */
export function onEnterCreateOrOpen(
  cursor: number,
  onCreate: () => void,
  onOpen: (dataIndex: number) => void,
): void {
  const dataIndex = dataIndexFromCursor(cursor);
  if (dataIndex === null) {
    onCreate();
    return;
  }
  onOpen(dataIndex);
}
