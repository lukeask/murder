import { ACTION_IDS, ACTIONS, type ActionId, type Modifier } from '../../../input/bindings.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const MODIFIERS: readonly Modifier[] = ['alt', 'ctrl', 'both'];

export const REBINDABLE: readonly ActionId[] = ACTION_IDS.filter((id) => ACTIONS[id].rebindable);

export const RESERVED_KEYS: ReadonlySet<string> = new Set([
  'c',
  'd',
  'z',
  '0',
  '1',
  '2',
  '3',
  '4',
  '5',
  '6',
  '7',
  '8',
  '9',
]);

const modifierItem: SettingsItem = {
  id: 'keybindings.modifier',
  label: 'Command Modifier',
  rows: () => [
    headerRow(modifierItem),
    ...MODIFIERS.map(
      (value): SettingsRow => ({ id: `keybindings.modifier:${value}`, kind: 'modifier', value }),
    ),
  ],
};

const vimItem: SettingsItem = {
  id: 'keybindings.vim',
  label: 'Vim Mode',
  rows: () => [
    headerRow(vimItem),
    { id: 'keybindings.vim:on', kind: 'vim', value: true },
    { id: 'keybindings.vim:off', kind: 'vim', value: false },
  ],
};

const bindingsItem: SettingsItem = {
  id: 'keybindings.bindings',
  label: 'Keybindings',
  rows: () => [
    headerRow(bindingsItem),
    ...REBINDABLE.map(
      (action): SettingsRow => ({ id: `keybindings.binding:${action}`, kind: 'binding', action }),
    ),
  ],
};

export const KEYBINDING_ITEMS: readonly SettingsItem[] = [modifierItem, vimItem, bindingsItem];
