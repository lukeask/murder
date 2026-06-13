/**
 * TicketDetail — the open ticket's frontmatter + editable body + schedule control, over the
 * `ticketDetail` slice. Opened by clicking a row in {@link TicketsPanel} (`ticketDetail.open`).
 *
 * Wired interactions (existing RPCs):
 *  - edit the body (`ticketDetail.setEditedBody`, local) then save (`ticketDetail.saveBody`).
 *  - set a schedule duration (`ticketDetail.setScheduleInput`, local + client validation) then
 *    `ticketDetail.schedule`.
 *  - close (`ticketDetail.close`).
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';

export function TicketDetail(): React.JSX.Element | null {
  const detail = useAppStore((s) => s.ticketDetail, shallow);
  const setEditedBody = useAppStore((s) => s.actions.ticketDetail.setEditedBody);
  const setScheduleInput = useAppStore((s) => s.actions.ticketDetail.setScheduleInput);
  const saveBody = useAppStore((s) => s.actions.ticketDetail.saveBody);
  const schedule = useAppStore((s) => s.actions.ticketDetail.schedule);
  const close = useAppStore((s) => s.actions.ticketDetail.close);

  if (detail.ticketId === null) {
    return null;
  }

  const fm = detail.frontmatter;
  const body = detail.editedBody ?? detail.savedBody ?? '';
  const dirty = detail.editedBody !== null && detail.editedBody !== detail.savedBody;

  return (
    <div className="ticket-detail">
      <header className="doc-viewer__head">
        <span className="doc-viewer__title">
          <span className="doc-viewer__kind">ticket</span> {detail.ticketId}
        </span>
        <button type="button" className="row-action" onClick={() => close()}>
          close
        </button>
      </header>

      {detail.status === 'loading' ? (
        <p className="panel__hint">Loading…</p>
      ) : detail.status === 'error' ? (
        <p className="panel__hint panel__hint--error">{detail.error ?? 'Failed to load.'}</p>
      ) : (
        <div className="ticket-detail__body">
          {fm !== null ? (
            <dl className="ticket-detail__fm">
              <dt>title</dt>
              <dd>{fm.title}</dd>
              <dt>status</dt>
              <dd>{fm.status}</dd>
              <dt>deps</dt>
              <dd>{fm.deps || '—'}</dd>
              <dt>harness</dt>
              <dd>{fm.harness ?? '—'}</dd>
              <dt>model</dt>
              <dd>{fm.model ?? '—'}</dd>
              <dt>worktree</dt>
              <dd>{fm.worktree ?? '—'}</dd>
            </dl>
          ) : null}

          <div className="ticket-detail__schedule">
            <label>
              schedule in
              <input
                type="text"
                placeholder="1d4h3m"
                value={detail.scheduleInput}
                onChange={(e) => setScheduleInput(e.target.value)}
                aria-invalid={detail.scheduleInput !== '' && !detail.scheduleValid}
              />
            </label>
            <button
              type="button"
              className="row-action"
              disabled={!detail.scheduleValid || detail.scheduleInput === ''}
              onClick={() => void schedule()}
            >
              schedule
            </button>
          </div>

          <textarea
            className="ticket-detail__editor"
            value={body}
            onChange={(e) => setEditedBody(e.target.value)}
            spellCheck={false}
          />
          <button
            type="button"
            className="row-action"
            disabled={!dirty || detail.status === 'saving'}
            onClick={() => void saveBody()}
          >
            {detail.status === 'saving' ? 'saving…' : 'save body'}
          </button>
        </div>
      )}
    </div>
  );
}
