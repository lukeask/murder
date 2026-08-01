import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const GAP_OPTIONS: readonly number[] = [0, 1, 2, 3, 4];

/** Radio steps for Background Transparency (0 = solid, 100 = terminal default). */
export const BACKGROUND_TRANSPARENCY_OPTIONS: readonly number[] = [0, 25, 50, 75, 100];

const themeItem: SettingsItem = {
  id: 'appearance.theme',
  label: 'Theme',
  rows: (context) => [
    headerRow(themeItem),
    ...context.themes.map(
      (theme): SettingsRow => ({
        id: `appearance.theme:${theme.id}`,
        kind: 'theme',
        value: theme.id,
        name: theme.name,
        builtin: theme.builtin,
      }),
    ),
    { id: 'appearance.themeImport', kind: 'themeImport' },
  ],
};

const paneGapItem: SettingsItem = {
  id: 'appearance.paneGap',
  label: 'Pane Gap',
  rows: () => [
    headerRow(paneGapItem),
    ...GAP_OPTIONS.map(
      (value): SettingsRow => ({ id: `appearance.paneGap:${value}`, kind: 'gap', value }),
    ),
  ],
};

const backgroundTransparencyItem: SettingsItem = {
  id: 'appearance.backgroundTransparency',
  label: 'Background Transparency',
  rows: () => [
    headerRow(backgroundTransparencyItem),
    ...BACKGROUND_TRANSPARENCY_OPTIONS.map(
      (value): SettingsRow => ({
        id: `appearance.backgroundTransparency:${value}`,
        kind: 'bgTransparency',
        value,
      }),
    ),
  ],
};

const defaultChatViewItem: SettingsItem = {
  id: 'appearance.defaultChatView',
  label: 'Default Chat View',
  rows: () => [
    headerRow(defaultChatViewItem),
    {
      id: 'appearance.defaultChatView:verbose',
      kind: 'chatView',
      value: 'verbose',
    },
    {
      id: 'appearance.defaultChatView:condensed',
      kind: 'chatView',
      value: 'condensed',
    },
  ],
};

const documentDisplayItem: SettingsItem = {
  id: 'appearance.documentDisplay',
  label: 'Document Display',
  rows: () => [
    headerRow(documentDisplayItem),
    {
      id: 'appearance.documentDisplay:plain',
      kind: 'documentDisplay',
      value: 'plain',
    },
    {
      id: 'appearance.documentDisplay:markdown',
      kind: 'documentDisplay',
      value: 'markdown',
    },
  ],
};

export const APPEARANCE_ITEMS: readonly SettingsItem[] = [
  themeItem,
  paneGapItem,
  backgroundTransparencyItem,
  defaultChatViewItem,
  documentDisplayItem,
];
