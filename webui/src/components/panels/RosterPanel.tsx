/**
 * RosterPanel (crows) — the live agent roster, grouped by kind (collaborator / planners / rogue /
 * ticket) with per-crow health and favorite stars. Maps to the `roster` + `favorites` slices via
 * {@link selectCrowsView}; clicking a crow selects it as the active chat target (the conversations
 * slice's `setActivePaneAgentId`), and the ★ toggles its favorite (`favorites.toggle`). A
 * ticket-bound crow can be reset via `roster.resetCrow(ticketId)`.
 */

import { selectCrowsView } from '@core/selectors/crowsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel } from '../Panel.js';
import { SliceHint } from '../SliceHint.js';

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

  return (
    <Panel title="Crows">
      <SliceHint state={view} empty="No agents." />
      {view.sections.map((section) => (
        <div key={section.group} className="roster__section">
          <div className="roster__section-label">{section.label}</div>
          <ul className="list">
            {section.rows.map((row) => {
              const ticketId = ticketIdFor(row.agentId);
              return (
                <li
                  key={row.agentId}
                  className="list__row roster__row"
                  data-selected={row.agentId === activeAgentId ? 'true' : undefined}
                  onClick={() => {
                    setActivePane(row.agentId);
                    setPaneOpen(row.agentId, true);
                  }}
                >
                  <button
                    type="button"
                    className="star"
                    aria-pressed={row.favorited}
                    title={row.favorited ? 'Unstar' : 'Star'}
                    onClick={(e) => {
                      e.stopPropagation();
                      void toggleFavorite(row.agentId);
                    }}
                  >
                    {row.favorited ? '★' : '☆'}
                  </button>
                  <span className="list__primary roster__name">{row.name}</span>
                  <span className="roster__meta">{row.harness}</span>
                  <span className={`health health--${row.health}`} title={`health: ${row.health}`} />
                  <span className={`badge badge--${row.status}`}>{row.status}</span>
                  {ticketId !== null ? (
                    <button
                      type="button"
                      className="row-action"
                      title="Reset crow"
                      onClick={(e) => {
                        e.stopPropagation();
                        void resetCrow(ticketId);
                      }}
                    >
                      reset
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </Panel>
  );
}
