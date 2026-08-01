/**
 * HelpDialog — keybind / chat-command help for the web cockpit.
 * Groups come from shared {@link buildHelpGroups} (live bindings + keymap registry) plus a
 * Composer section for browser-only image paste / send chords.
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { resolveBindings, type ActionId } from '@murder/ui-core/input/bindings.js';
import { createKeymapRegistry } from '@murder/ui-core/input/keymapRegistry.js';
import {
  buildHelpGroups,
  type HelpEntry,
  type HelpGroup,
} from '@murder/ui-core/selectors/helpGroups.js';
import { useMemo } from 'react';
import { Dialog } from '../ds/index.js';

export type { HelpEntry, HelpGroup };

const EMPTY_REGISTRY = createKeymapRegistry();

/** Composer conventions unique to the browser surface (not in the TUI keymap registry). */
const COMPOSER_GROUP: HelpGroup = {
  title: 'Composer',
  entries: [
    { key: 'paste image', description: 'attach clipboard image' },
    { key: 'Enter', description: 'send' },
    { key: 'S-Enter', description: 'newline' },
  ],
};

const CROWS_GROUP: HelpGroup = {
  title: 'Crows',
  entries: [
    { key: 'click', description: 'select chat target' },
    { key: '★', description: 'toggle favorite' },
    { key: 'murder', description: 'arm kill confirm for a crow' },
    { key: 'm', description: 'confirm murder (while armed)' },
  ],
};

/**
 * Web help groups: shared live bindings (modifier + overrides) plus Composer / Crows affordances.
 * `:help` description is adjusted to "this dialog" for the browser surface.
 */
export function buildWebHelpGroups(
  modifier: 'alt' | 'ctrl' | 'both' = 'alt',
  overrides: Readonly<Partial<Record<ActionId, string>>> = {},
): readonly HelpGroup[] {
  const bindings = resolveBindings(modifier, true, overrides);
  const shared = buildHelpGroups(bindings, EMPTY_REGISTRY).map((group) => {
    if (group.title !== 'Commands') {
      return group;
    }
    return {
      ...group,
      entries: group.entries.map((entry) =>
        entry.key === ':help' ? { ...entry, description: 'this dialog' } : entry,
      ),
    };
  });
  const globalIdx = shared.findIndex((g) => g.title === 'Global');
  const insertAt = globalIdx >= 0 ? globalIdx + 1 : 0;
  return [
    ...shared.slice(0, insertAt),
    COMPOSER_GROUP,
    CROWS_GROUP,
    ...shared.slice(insertAt),
  ];
}

export interface HelpDialogProps {
  readonly open?: boolean;
  readonly onClose: () => void;
  /** Override groups (tests). */
  readonly groups?: readonly HelpGroup[];
}

export function HelpDialog({
  open = true,
  onClose,
  groups,
}: HelpDialogProps): React.JSX.Element {
  const modifier = useAppStore((s) => s.settings.modifier);
  const keyOverrides = useAppStore((s) => s.settings.keyOverrides);
  const resolved = useMemo(
    () => groups ?? buildWebHelpGroups(modifier, keyOverrides),
    [groups, modifier, keyOverrides],
  );
  return (
    <Dialog open={open} title="Help" onClose={onClose} className="help-dialog">
      <div className="help">
        {resolved.map((group) => (
          <section key={group.title} className="help__group">
            <h3 className="help__heading">{group.title}</h3>
            <ul className="help__list">
              {group.entries.map((entry) => (
                <li key={`${group.title}:${entry.key}`} className="help__row">
                  <kbd className="help__key">{entry.key}</kbd>
                  <span className="help__desc">{entry.description}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
