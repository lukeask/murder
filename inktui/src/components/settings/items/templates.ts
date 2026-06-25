import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const TEMPLATE_NAME_RE = /^[A-Za-z0-9_-]+$/;

const templatesItem: SettingsItem = {
  id: 'templates.manage',
  label: 'Templates',
  rows: ({ templates }) => [
    headerRow(templatesItem),
    { id: 'templates.create', kind: 'templateCreate' },
    ...(templates.length === 0
      ? ([{ id: 'templates.empty', kind: 'templateEmpty' }] satisfies readonly SettingsRow[])
      : templates.map(
          (template): SettingsRow => ({
            id: `templates.template:${template.name}`,
            kind: 'template',
            name: template.name,
          }),
        )),
  ],
};

export const TEMPLATE_ITEMS: readonly SettingsItem[] = [templatesItem];
