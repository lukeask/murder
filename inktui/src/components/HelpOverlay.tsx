/**
 * `HelpOverlay` — the keybinding help overlay (item 12): a **modal mode** (the
 * {@link ./SpawnWizardModal.js} / {@link ./SettingsModal.js} mode-factory idiom — `helpMode(...)`,
 * `presentation: 'modal'`, rendered through the {@link ./Overlay.js Overlay}) that lists every
 * current bind, grouped by scope (global / panel / mode).
 *
 * ## Labels track the live settings
 *
 * Global binds are labelled from the **resolved** bindings ({@link ../input/bindings.js}
 * `label`/`chordsFor`), so the overlay tracks the user's modifier choice (alt/ctrl/both) and any
 * rebinds — open it after switching the modifier and the chords re-read. Panel binds come from the
 * live {@link ../input/keymapRegistry.js keymap registry} (each registered panel's declared entries
 * with their descriptions). The `mode` group documents the conventions shared by the modal surfaces
 * (their per-mode keymaps only register while that mode is up, so they are listed statically here).
 *
 * ## Paging
 *
 * The entries are paged so the modal never overflows: `h`/`l`/`←`/`→` step pages when the content
 * exceeds one page. The bottom-bar hints ({@link ../input/modeStore.js Mode.hints}) reflect this —
 * just `esc` on a single page, `h/l ←→ pages · esc quit` when multi-paged.
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useModalWidth } from '../hooks/useTerminalSize.js';
import type { ResolvedBindings } from '@murder/ui-core/input/bindings.js';
import type { KeymapRegistryApi } from '@murder/ui-core/input/keymapRegistry.js';
import {
  buildHelpGroups,
  type HelpEntry,
  type HelpGroup,
} from '@murder/ui-core/selectors/helpGroups.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';

export type { HelpEntry, HelpGroup };
export { buildHelpGroups };

/** The stable mode id for idempotent re-enter. */
export const HELP_MODE_ID = 'keyHelp';

/** How many entry rows (across all groups, headers excluded) fit on one page. Conservative so the
 * modal stays within a small terminal even after its border + per-group headings.
 *
 * Left as a constant rather than derived from the live terminal height: pagination is computed once
 * in {@link helpMode} (a non-React closure built at open), so it has no live `rows` to read, and the
 * page count must stay stable across the open (re-paginating on resize would jump the user's page
 * index). 14 fits comfortably above the app's own min-terminal floor (16 rows) once the
 * border + headings are accounted for; if the help surface grows much larger, thread the live row
 * count into {@link paginateHelp} at the {@link helpMode} call site instead. */
const ROWS_PER_PAGE = 14;

/** Split the grouped entries into pages of at most {@link ROWS_PER_PAGE} entry rows. A group is kept
 * whole on a page where it fits; an oversized group is split across pages (its heading repeats). Pure
 * — exported for unit tests. Always returns at least one (possibly empty) page. */
export function paginateHelp(
  groups: readonly HelpGroup[],
  rowsPerPage = ROWS_PER_PAGE,
): readonly (readonly HelpGroup[])[] {
  const pages: HelpGroup[][] = [];
  let current: HelpGroup[] = [];
  let used = 0;
  for (const group of groups) {
    let rest = group.entries;
    let first = true;
    while (rest.length > 0) {
      const room = rowsPerPage - used;
      if (room <= 0) {
        pages.push(current);
        current = [];
        used = 0;
        continue;
      }
      const take = rest.slice(0, room);
      current.push({ title: first ? group.title : `${group.title} (cont.)`, entries: take });
      used += take.length;
      rest = rest.slice(room);
      first = false;
    }
  }
  if (current.length > 0 || pages.length === 0) {
    pages.push(current);
  }
  return pages;
}

/** Options for the help mode factory. */
export interface HelpModeOptions {
  /** Called when the modal is dismissed (after the mode exits). */
  readonly onDismiss?: () => void;
}

/** Mutable closure state — not React state. `render` reads it; `onIntent` mutates it. */
interface HelpState {
  /** The current page index. */
  page: number;
  /** The paginated groups (computed once at open from the live bindings + registry). */
  readonly pages: readonly (readonly HelpGroup[])[];
}

/** The mode's intent union — paging + dismiss. */
type HelpIntent = 'pagePrev' | 'pageNext' | 'cancel';

/**
 * Build the help-overlay {@link Mode}. Enter via
 * `modes.getState().enter(helpMode(modes, bindings, registry))`, where `bindings` is the live
 * resolved table and `registry` the keymap registry — so the overlay opens reflecting the current
 * modifier choice, rebinds, and the panels that are actually registered.
 */
export function helpMode(
  modes: ModeStoreApi,
  bindings: ResolvedBindings,
  registry: KeymapRegistryApi,
  opts: HelpModeOptions = {},
): Mode<HelpIntent> {
  const id = HELP_MODE_ID;
  const pages = paginateHelp(buildHelpGroups(bindings, registry));
  const s: HelpState = { page: 0, pages };
  const multiPage = pages.length > 1;

  function refresh(): void {
    const frame = modes.getState().stack.find((f) => f.mode.id === id);
    if (frame !== undefined) {
      modes.getState().enter(frame.mode);
    }
  }

  function step(delta: number): void {
    if (!multiPage) {
      return;
    }
    const len = s.pages.length;
    s.page = (((s.page + delta) % len) + len) % len;
    refresh();
  }

  function dismiss(): void {
    modes.getState().exit(id);
    opts.onDismiss?.();
  }

  return {
    id,
    presentation: 'modal',
    // Single page → just `esc`; multi-page → paging + quit (item 12's bottom-bar spec).
    hints: multiPage
      ? [
          { key: 'h/l ←→', description: 'pages' },
          { key: 'esc', description: 'quit' },
        ]
      : [{ key: 'esc', description: 'quit' }],
    keymap: [
      {
        chord: [{ input: 'h' }, { key: { leftArrow: true } }],
        intent: 'pagePrev',
        description: 'prev page',
      },
      {
        chord: [{ input: 'l' }, { key: { rightArrow: true } }],
        intent: 'pageNext',
        description: 'next page',
      },
      { chord: { key: { escape: true } }, intent: 'cancel', description: 'close' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'pagePrev':
          step(-1);
          break;
        case 'pageNext':
          step(1);
          break;
        case 'cancel':
          dismiss();
          break;
        default:
          return intent satisfies never;
      }
    },
    render: () => <HelpDialog state={s} />,
  };
}

// ---------------------------------------------------------------------------------------------
// Presentation — pure of state (rule 1).
// ---------------------------------------------------------------------------------------------

function HelpDialog({ state: s }: { readonly state: HelpState }): JSX.Element {
  const theme = useTheme();
  // Design width 56, clamped to the live terminal so a narrow screen doesn't overflow the box.
  const width = useModalWidth(56);
  const groups = s.pages[s.page] ?? [];
  const multiPage = s.pages.length > 1;
  // The widest key label on this page → align the descriptions into a column.
  const keyWidth = Math.max(1, ...groups.flatMap((g) => g.entries.map((e) => e.key.length)));

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={width}
    >
      <Box justifyContent="space-between">
        <Text bold color={theme.heading}>
          Keybindings
        </Text>
        {multiPage && (
          <Text color={theme.muted}>
            {s.page + 1}/{s.pages.length}
          </Text>
        )}
      </Box>

      <Box marginTop={1} flexDirection="column">
        {groups.map((group) => (
          <Box key={group.title} flexDirection="column" marginTop={1}>
            <Text bold color={theme.accent}>
              {group.title}
            </Text>
            {group.entries.map((entry) => (
              <Box key={`${entry.key}:${entry.description}`}>
                <Text color={theme.warning}>{entry.key.padEnd(keyWidth)}</Text>
                <Text color={theme.text}>
                  {'  '}
                  {entry.description}
                </Text>
              </Box>
            ))}
          </Box>
        ))}
      </Box>
    </Box>
  );
}
