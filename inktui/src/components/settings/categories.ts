import { APPEARANCE_ITEMS } from './items/appearance.js';
import { BAR_ITEMS } from './items/bars.js';
import { HARNESS_ITEMS } from './items/harnesses.js';
import { KEYBINDING_ITEMS } from './items/keybindings.js';
import { LLM_ITEMS } from './items/llm.js';
import { TEMPLATE_ITEMS } from './items/templates.js';
import type {
  SettingsBuildContext,
  SettingsCategory,
  SettingsCategoryId,
  SettingsRow,
} from './types.js';

export const SETTINGS_CATEGORIES = [
  { id: 'appearance', label: 'Appearance', items: APPEARANCE_ITEMS },
  { id: 'bars', label: 'Bars', items: BAR_ITEMS },
  { id: 'harnesses', label: 'Harnesses', items: HARNESS_ITEMS },
  { id: 'llm', label: 'LLM', items: LLM_ITEMS },
  { id: 'templates', label: 'Templates', items: TEMPLATE_ITEMS },
  { id: 'keybindings', label: 'Keybindings', items: KEYBINDING_ITEMS },
] satisfies readonly [SettingsCategory, ...SettingsCategory[]];

export function categoryById(id: SettingsCategoryId): SettingsCategory {
  return SETTINGS_CATEGORIES.find((category) => category.id === id) ?? SETTINGS_CATEGORIES[0];
}

export function categoryIndexById(id: SettingsCategoryId): number {
  return Math.max(
    0,
    SETTINGS_CATEGORIES.findIndex((category) => category.id === id),
  );
}

export function buildCategoryRows(
  categoryId: SettingsCategoryId,
  context: SettingsBuildContext,
): readonly SettingsRow[] {
  return categoryById(categoryId).items.flatMap((item) => item.rows(context));
}
