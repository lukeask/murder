/**
 * RosterPanel (crows) — the live agent roster, grouped by kind (collaborator / planners / rogue /
 * ticket) with per-crow health and favorite stars. Maps to the `roster` + `favorites` slices via
 * {@link selectCrowsView}; clicking a crow selects it as the active chat target (the conversations
 * slice's `setActivePaneAgentId`), and the ★ toggles its favorite (`favorites.toggle`). A
 * ticket-bound crow can be reset via `roster.resetCrow(ticketId)`.
 *
 * Reskinned onto the DS (C2, follows the TicketsPanel exemplar): a DS {@link Panel} wraps each
 * type-section's {@link ListRow}s. Per row: an {@link Avatar} (name-hashed identity color) leads the
 * title, harness · model is the mono meta line, the crow's client-side `health` becomes a
 * {@link StatusDot} (green/yellow/red/neutral → running/pending/failed/idle, with the raw status word
 * as its label), the favorite star is the ListRow's own `starred`/`onPinToggle`, and the reset action
 * is a small ghost {@link IconButton}. Data wiring is byte-for-byte unchanged.
 */

import { selectCrowsView } from '@core/selectors/crowsSelectors.js';
import type { Health } from '@core/selectors/crowHealthSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import {
  Panel,
  ListRow,
  StatusDot,
  Avatar,
  Tag,
  IconButton,
  Icon,
  cx,
} from '../ds/index.js';
import type { StatusDotStatus } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

/** Map the selector's client-side crow health onto a DS StatusDot status (rule 2: no re-derivation
 * of meaning — this is the fixed health→dot color contract from the C2 spec). */
const HEALTH_TO_DOT: Readonly<Record<Health, StatusDotStatus>> = {
  green: 'running',
  yellow: 'pending',
  red: 'failed',
  neutral: 'idle',
};

export function RosterPanel(): React.JSX.Element {
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const rosterRows = useAppStore((s) => s.roster.rows);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const resetCrow = useAppStore((s) => s.actions.roster.resetCrow);
  const setActivePane = useAppStore((s) => s.actions.conversations.setActivePaneAgentId);
  const setPaneOpen = useAppStore((s) => s.actions.conversations.setChatPaneOpen);
  const activeAgentId = useAppStore((s) => s.conversations.activePaneAgentId);

  const view = selectCrowsView(roster, Date.now(), favorites);

  const ticketIdFor = (agentId: string): string | null =>
    rosterRows.find((r) => r.agentId === agentId)?.ticketId ?? null;

  const rowCount = view.sections.reduce((n, s) => n + s.rows.length, 0);

  return (
    <Panel title="Crows" count={view.isEmpty ? null : rowCount} flush>
      <SliceHint state={view} empty="No agents." />
      {view.sections.map((section) => (
        <div key={section.group} className="roster-section">
          <span className="roster-section__label">{section.label}</span>
          {section.rows.map((row) => {
            const ticketId = ticketIdFor(row.agentId);
            return (
              <ListRow
                key={row.agentId}
                as="button"
                selected={row.agentId === activeAgentId}
                starred={row.favorited}
                onPinToggle={() => void toggleFavorite(row.agentId)}
                onClick={() => {
                  setActivePane(row.agentId);
                  setPaneOpen(row.agentId, true);
                }}
                title={
                  <span className={cx('roster-name')}>
                    <Avatar size="sm" name={row.name} />
                    {row.name}
                  </span>
                }
                meta={
                  <span className="roster-meta">
                    <Tag>{row.harness}</Tag>
                    <span>{row.model}</span>
                  </span>
                }
                trailing={
                  <span className="roster-trail">
                    <StatusDot status={HEALTH_TO_DOT[row.health]} pulse label={row.status} />
                    {ticketId !== null ? (
                      <IconButton
                        size="sm"
                        label="Reset crow"
                        onClick={(e) => {
                          e.stopPropagation();
                          void resetCrow(ticketId);
                        }}
                      >
                        <Icon name="back" size={14} />
                      </IconButton>
                    ) : null}
                  </span>
                }
              />
            );
          })}
        </div>
      ))}
    </Panel>
  );
}
