/**
 * TicketsPanel — the schedule/tickets list. EXEMPLAR for the design-system panel reskin (Phase C1):
 * the C2 panel-group agents copy this composition shape.
 *
 * Data wiring is UNCHANGED: still binds the `tickets` slice via {@link selectTicketsView}, still opens
 * the ticket detail via `ticketDetail.open` (surfaced in the Stage), still derives status color from
 * the selector's `statusTone` (rule 2 — no string-matching here). Only the PRESENTATION moved onto the
 * DS primitives: the DS {@link Panel} container (titled + count), a {@link ListRow} per ticket (title /
 * meta line / trailing status), and a {@link Badge} for the status (tone = the selector's `statusTone`,
 * which is already one of the DS semantic tones success/warning/error/neutral).
 *
 * ── THE LOCKED PANEL-REWRITE PATTERN (C2 follows this) ──────────────────────────────────────────
 *  1. Import DS primitives from the barrel: `from '../ds/index.js'` (the `@core` alias is for the
 *     portable store/selectors only; the DS lives in THIS app under `src/components/ds`). Drop the
 *     old panel-local `../Panel.js` and `.list*` markup.
 *  2. Keep the slice selector + actions exactly. Lifecycle (loading/error/empty) stays {@link SliceHint}.
 *  3. Compose: `<Panel title count flush>` → one `<ListRow as="button">` per row, mapping the
 *     selector's display-ready cells onto title / meta / trailing. Selection = `selected={…}`; click =
 *     the existing action. Status/semantics = `<Badge tone={row.someTone}>` — never re-derive tone.
 *  4. Bespoke CSS goes in the group's OWN `styles/panels-<group>.css` (see panels.css banner). Shared
 *     helpers only live in `panels.css`.
 */

import { selectTicketsView } from '@core/selectors/ticketsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, ListRow, Badge, Tag } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

export function TicketsPanel(): React.JSX.Element {
  const tickets = useAppStore((s) => s.tickets, shallow);
  const openDetail = useAppStore((s) => s.actions.ticketDetail.open);
  const openId = useAppStore((s) => s.ticketDetail.ticketId);
  const view = selectTicketsView(tickets);

  return (
    <Panel title="tickets" count={view.isEmpty ? null : view.rows.length} flush>
      <SliceHint state={view} empty="No tickets." />
      {view.rows.map((row) => (
        <ListRow
          key={row.id}
          as="button"
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
