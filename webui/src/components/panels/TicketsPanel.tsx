/**
 * TicketsPanel — the schedule/tickets list. Maps to the `tickets` slice via {@link selectTicketsView}.
 * Clicking a row opens the ticket detail (`ticketDetail.open`), surfaced in the Stage as a doc-like
 * pane. The status tone comes from the selector (`statusTone`); the component only maps it to a CSS
 * class — no string matching here (rule 2).
 */

import { selectTicketsView } from '@core/selectors/ticketsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel } from '../Panel.js';
import { SliceHint } from '../SliceHint.js';

export function TicketsPanel(): React.JSX.Element {
  const tickets = useAppStore((s) => s.tickets, shallow);
  const openDetail = useAppStore((s) => s.actions.ticketDetail.open);
  const openId = useAppStore((s) => s.ticketDetail.ticketId);
  const view = selectTicketsView(tickets);

  return (
    <Panel title="Tickets">
      <SliceHint state={view} empty="No tickets." />
      <ul className="list">
        {view.rows.map((row) => (
          <li
            key={row.id}
            className="list__row ticket__row"
            data-selected={row.id === openId ? 'true' : undefined}
            onClick={() => void openDetail(row.id)}
          >
            <div className="ticket__main">
              <span className="list__primary">{row.titleCell}</span>
              <span className="list__secondary">{row.idCell}</span>
            </div>
            <div className="ticket__cols">
              <span className={`tone tone--${row.statusTone}`}>{row.statusCell}</span>
              <span className="ticket__cell">{row.lastUpdateCell}</span>
              <span className={row.depsSatisfied ? 'ticket__cell tone--success' : 'ticket__cell tone--warning'}>
                {row.depsCell}
              </span>
              <span className="ticket__cell">{row.scheduleCell}</span>
              <span className="ticket__cell ticket__cell--dim">{row.harnessCell}</span>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
