/**
 * TicketsPanel — flat schedule/tickets list over `tickets` via {@link selectTicketsView}.
 * Prefer {@link WorkflowsPanel} for the main rail (run-first tree). Kept for ticket-only surfaces.
 */

import { selectTicketsView } from '@murder/ui-core/selectors/ticketsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCreationDialogs } from '../../creationDialogs.js';
import { Panel, ListRow, Badge, Tag, IconButton, Icon } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

export function TicketsPanel(): React.JSX.Element {
  const tickets = useAppStore((s) => s.tickets, shallow);
  const openDetail = useAppStore((s) => s.actions.ticketDetail.open);
  const openId = useAppStore((s) => s.ticketDetail.ticketId);
  const { openTicket } = useCreationDialogs();
  const view = selectTicketsView(tickets);

  return (
    <Panel
      title="tickets"
      count={view.isEmpty ? null : view.rows.length}
      flush
      data-panel-id="tickets"
      actions={
        <IconButton label="New ticket" onClick={openTicket}>
          <Icon name="plus" size={14} />
        </IconButton>
      }
    >
      <SliceHint state={view} empty="No tickets." />
      {view.rows.map((row) => (
        <ListRow
          key={row.id}
          selected={row.id === openId}
          onClick={() => void openDetail(row.id)}
          title={row.titleCell}
          meta={
            <span className="ticket-meta">
              <span className="ticket-meta__id">{row.idCell}</span>
              <span className="ticket-meta__cell">{row.lastUpdateCell}</span>
              <span
                className={
                  row.depsSatisfied
                    ? 'ticket-meta__cell tone--success'
                    : 'ticket-meta__cell tone--warning'
                }
              >
                {row.depsCell}
              </span>
              <span className="ticket-meta__cell">{row.scheduleCell}</span>
              {row.harnessCell.length > 0 ? <Tag>{row.harnessCell}</Tag> : null}
            </span>
          }
          trailing={
            <Badge tone={row.statusTone} dot>
              {row.statusCell}
            </Badge>
          }
        />
      ))}
    </Panel>
  );
}
