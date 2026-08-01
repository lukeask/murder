import type { SettingsItem } from '../types.js';
import { headerRow } from '../types.js';

/** Re-export for callers that previously imported from this module. */
export { TEMPLATE_NAME_RE } from '../../promptTemplates/refs.js';

const templatesItem: SettingsItem = {
  id: 'templates.manage',
  label: 'Prompt Templates',
  rows: () => [
    headerRow(templatesItem),
    { id: 'templates.open', kind: 'templateOpen' },
  ],
};

export const TEMPLATE_ITEMS: readonly SettingsItem[] = [templatesItem];
