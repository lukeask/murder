import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const WORKSPACE_COUNT_OPTIONS: readonly number[] = [1, 2, 3, 4, 5, 6, 7, 8, 9];

const workspaceCountItem: SettingsItem = {
  id: 'workspaces.count',
  label: 'Workspaces',
  rows: () => [
    headerRow(workspaceCountItem),
    ...WORKSPACE_COUNT_OPTIONS.map(
      (value): SettingsRow => ({
        id: `workspaces.count:${value}`,
        kind: 'workspaceCount',
        value,
      }),
    ),
  ],
};

export const WORKSPACE_ITEMS: readonly SettingsItem[] = [workspaceCountItem];
