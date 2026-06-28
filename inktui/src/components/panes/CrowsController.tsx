import { memo, useCallback, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { deriveAgentIdentity } from '../../selectors/agentIdentity.js';
import { isChatPaneOpen } from '../../selectors/conversationsSelectors.js';
import { type CrowsView, useCrowsView } from '../../selectors/crowsSelectors.js';
import { murderConfirmStore, resetConfirmStore } from '../../store/murder/murderConfirmStore.js';
import { toastStore } from '../../store/toast/toastStore.js';
import { useTheme } from '../../theme/themeStore.js';
import { CrowsSurface, type CrowsSurfaceRow, type CrowsSurfaceStatus } from './CrowsSurface.js';
import { MeasuredPaneFrame, useClampedCursor } from './shared/index.js';

type CrowsIntent =
  | 'cursorDown'
  | 'cursorUp'
  | 'refresh'
  | 'toggleExpanded'
  | 'star'
  | 'openChat'
  | 'murder'
  | 'reset';

export function crowsSurfaceRowsFromView(view: CrowsView): readonly CrowsSurfaceRow[] {
  return view.sections.flatMap((section) =>
    section.rows.map((row) => ({
      id: row.agentId,
      group: section.label,
      name: row.name,
      meta: `${row.harness} · ${row.model}`,
      working: row.working,
      starred: row.favorited,
      health: row.health,
    })),
  );
}

function surfaceStatus(status: CrowsView['status']): CrowsSurfaceStatus {
  return status === 'loading' || status === 'error' ? status : 'idle';
}

export interface CrowsControllerProps {
  readonly presentation: PanePresentation;
}

export const CrowsController = memo(function CrowsController({
  presentation,
}: CrowsControllerProps): React.JSX.Element {
  const roster = useAppStore((state) => state.roster, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const conversations = useAppStore((state) => state.conversations, shallow);
  const refresh = useAppStore((state) => state.actions.roster.refresh);
  const resetCrow = useAppStore((state) => state.actions.roster.resetCrow);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const setActivePane = useAppStore((state) => state.actions.conversations.setActivePaneAgentId);
  const toggleChatPane = useAppStore((state) => state.actions.conversations.toggleChatPane);
  const bindings = useBindings();
  const view = useCrowsView(roster, favorites);
  const theme = useTheme();
  const rows = useMemo(() => crowsSurfaceRowsFromView(view), [view]);
  const { cursor, moveDown, moveUp } = useClampedCursor(rows.length);
  const [expanded, setExpanded] = useState(false);

  const agentIdAtCursor = useCallback((): string | null => {
    return rows[cursor]?.id ?? null;
  }, [cursor, rows]);

  const nameAtCursor = useCallback(
    (agentId: string): string => rows.find((row) => row.id === agentId)?.name ?? agentId,
    [rows],
  );

  const openChatAtCursor = useCallback(() => {
    const agentId = agentIdAtCursor();
    if (agentId === null) {
      return;
    }
    const rosterRow = roster.rows.find((row) => row.agentId === agentId);
    const identity = rosterRow === undefined ? null : deriveAgentIdentity(rosterRow);
    if (identity === null) {
      return;
    }
    const currentlyOpen = isChatPaneOpen(identity, favorites, conversations.paneOverrides);
    toggleChatPane(agentId, currentlyOpen);
    if (!currentlyOpen) {
      setActivePane(agentId);
    }
  }, [agentIdAtCursor, conversations, favorites, roster, setActivePane, toggleChatPane]);

  const keymap: PanelKeymap<CrowsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next crow',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev crow',
        },
        { chord: bindings.chordsFor('global.murder'), intent: 'murder', description: 'murder' },
        { chord: { key: { return: true } }, intent: 'openChat', description: 'toggle chat pane' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 'm' }, intent: 'toggleExpanded', description: 'toggle maximized' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        {
          chord: bindings.chordsFor('panel.resetCrow'),
          intent: 'reset',
          description: 'reset crow',
        },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            moveDown();
            return;
          case 'cursorUp':
            moveUp();
            return;
          case 'refresh':
            void refresh();
            return;
          case 'toggleExpanded':
            setExpanded((current) => !current);
            return;
          case 'openChat':
            openChatAtCursor();
            return;
          case 'star': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              void toggleFavorite(agentId);
              setActivePane(agentId);
            }
            return;
          }
          case 'murder': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              murderConfirmStore.getState().arm({ agentId, name: nameAtCursor(agentId) });
            }
            return;
          }
          case 'reset': {
            const agentId = agentIdAtCursor();
            if (agentId === null) {
              return;
            }
            const ticketId = roster.rows.find((row) => row.agentId === agentId)?.ticketId ?? null;
            if (ticketId === null) {
              toastStore.getState().push('no ticket to reset for this row', { ttlMs: 4000 });
              return;
            }
            const name = nameAtCursor(agentId);
            const pending = resetConfirmStore.getState().pending;
            if (pending !== null && pending.ticketId === ticketId) {
              resetConfirmStore.getState().clear();
              void resetCrow(ticketId)
                .then(() => {
                  toastStore.getState().push(`reset ${pending.name} → ready`, { ttlMs: 6000 });
                })
                .catch((error: unknown) => {
                  const message = error instanceof Error ? error.message : String(error);
                  toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
                });
              return;
            }
            resetConfirmStore.getState().arm({ ticketId, name });
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [
      agentIdAtCursor,
      bindings,
      moveDown,
      moveUp,
      nameAtCursor,
      openChatAtCursor,
      refresh,
      resetCrow,
      roster,
      setActivePane,
      toggleFavorite,
    ],
  );
  usePanelKeymap('crows', keymap);

  return (
    <MeasuredPaneFrame id="crows" presentation={presentation}>
      <CrowsSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={rows}
        cursor={cursor}
        expanded={expanded}
        status={surfaceStatus(view.status)}
        error={view.error}
      />
    </MeasuredPaneFrame>
  );
});
