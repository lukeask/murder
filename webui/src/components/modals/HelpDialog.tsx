/**
 * HelpDialog — keybind / chat-command help for the web cockpit.
 * Groups come from shared {@link buildHelpGroups} (live bindings + web panel keymap registry) plus a
 * Composer section for browser-only image paste / send chords. Multi-page via {@link paginateHelp}.
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { chordLabel, resolveBindings, type ActionId } from '@murder/ui-core/input/bindings.js';
import {
  buildHelpGroups,
  HELP_ROWS_PER_PAGE,
  paginateHelp,
  type HelpEntry,
  type HelpGroup,
} from '@murder/ui-core/selectors/helpGroups.js';
import { useEffect, useMemo, useState } from 'react';
import { Dialog } from '../ds/index.js';
import {
  createWebHelpKeymapRegistry,
  WEB_STAGE_DOC_KEYMAP,
  WEB_STAGE_TRANSCRIPT_KEYMAP,
} from '../../panelHelpKeymaps.js';
import { helpDialogHints, publishModeHints } from '../../keybindModeHints.js';

export type { HelpEntry, HelpGroup };

const WEB_HELP_REGISTRY = createWebHelpKeymapRegistry();

/** Composer conventions unique to the browser surface (not in the TUI keymap registry). */
const COMPOSER_GROUP: HelpGroup = {
  title: 'Composer',
  entries: [
    { key: 'paste image', description: 'attach clipboard image' },
    { key: 'Enter', description: 'send' },
    { key: 'S-Enter', description: 'newline' },
  ],
};

/** Browser affordances not in the crows keymap registry (click / armed confirm). */
const CROWS_GROUP: HelpGroup = {
  title: 'Crows',
  entries: [
    { key: 'click', description: 'select chat target' },
    { key: '★', description: 'toggle favorite' },
    { key: 'm (armed)', description: 'confirm murder' },
  ],
};

function keymapHelpGroup(title: string, keymap: typeof WEB_STAGE_DOC_KEYMAP): HelpGroup {
  return {
    title,
    entries: keymap
      .filter((e) => e.hidden !== true)
      .map((entry) => ({
        key: chordLabel(Array.isArray(entry.chord) ? entry.chord[0] : entry.chord),
        description: entry.description,
      })),
  };
}

/**
 * Web help groups: shared live bindings (modifier + overrides) plus Composer / Crows / Stage
 * affordances. `:help` description is adjusted to "this dialog" for the browser surface.
 */
export function buildWebHelpGroups(
  modifier: 'alt' | 'ctrl' | 'both' = 'alt',
  overrides: Readonly<Partial<Record<ActionId, string>>> = {},
): readonly HelpGroup[] {
  const bindings = resolveBindings(modifier, true, overrides);
  const shared = buildHelpGroups(bindings, WEB_HELP_REGISTRY).map((group) => {
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
    keymapHelpGroup('Stage document', WEB_STAGE_DOC_KEYMAP),
    keymapHelpGroup('Stage transcript', WEB_STAGE_TRANSCRIPT_KEYMAP),
    ...shared.slice(insertAt),
  ];
}

export interface HelpDialogProps {
  readonly open?: boolean;
  readonly onClose: () => void;
  /** Override groups (tests). */
  readonly groups?: readonly HelpGroup[];
  /** Rows per page for {@link paginateHelp}. */
  readonly rowsPerPage?: number;
}

export function HelpDialog({
  open = true,
  onClose,
  groups,
  rowsPerPage = HELP_ROWS_PER_PAGE,
}: HelpDialogProps): React.JSX.Element {
  const modifier = useAppStore((s) => s.settings.modifier);
  const keyOverrides = useAppStore((s) => s.settings.keyOverrides);
  const resolved = useMemo(
    () => groups ?? buildWebHelpGroups(modifier, keyOverrides),
    [groups, modifier, keyOverrides],
  );
  const pages = useMemo(() => paginateHelp(resolved, rowsPerPage), [resolved, rowsPerPage]);
  const [page, setPage] = useState(0);
  const multiPage = pages.length > 1;

  useEffect(() => {
    setPage(0);
  }, [resolved, rowsPerPage]);

  useEffect(() => {
    if (!open) return;
    return publishModeHints(helpDialogHints(multiPage));
  }, [open, multiPage]);

  useEffect(() => {
    if (!open || !multiPage) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      const len = pages.length;
      if (e.key === 'h' || e.key === 'ArrowLeft') {
        e.preventDefault();
        setPage((p) => (((p - 1) % len) + len) % len);
        return;
      }
      if (e.key === 'l' || e.key === 'ArrowRight') {
        e.preventDefault();
        setPage((p) => (p + 1) % len);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, multiPage, pages.length]);

  const pageGroups = pages[Math.min(page, pages.length - 1)] ?? [];

  return (
    <Dialog open={open} title="Help" onClose={onClose} className="help-dialog">
      <div className="help">
        {multiPage ? (
          <p className="help__page">
            page {page + 1}/{pages.length}
            <span className="help__page-hint"> · h/l ←→ pages</span>
          </p>
        ) : null}
        {pageGroups.map((group) => (
          <section key={`${page}:${group.title}`} className="help__group">
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
