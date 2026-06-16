/**
 * TicketDetail — the open ticket's frontmatter + editable body + schedule control, reskinned onto the
 * DS: an `active`/`flush` DS Panel (kind Tag + id title, close IconButton action); the frontmatter as a
 * clean key/value grid (no middots); a schedule DS Input + apply Button; a mono body <textarea> on a
 * raised surface with a focus ring; and a primary save Button. Data wiring is UNCHANGED (rule 2): all
 * `ticketDetail` state + its five actions (setEditedBody / setScheduleInput / saveBody / schedule /
 * close) and the same dirty / validity gating.
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, Tag, Input, Button, IconButton, Icon } from '../ds/index.js';

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
  const scheduleInvalid = detail.scheduleInput !== '' && !detail.scheduleValid;

  return (
    <div className="mds-ticket">
      <Panel
        active
        flush
        title={
          <span className="mds-ticket__title">
            <Tag tone="accent">ticket</Tag>
            <span>{detail.ticketId}</span>
          </span>
        }
        actions={
          <IconButton label="close" size="md" onClick={() => close()}>
            <Icon name="x" />
          </IconButton>
        }
      >
        {detail.status === 'loading' ? (
          <p className="mds-stage__empty">Loading…</p>
        ) : detail.status === 'error' ? (
          <p className="mds-stage__empty">{detail.error ?? 'Failed to load.'}</p>
        ) : (
          <>
            {fm !== null ? (
              <dl className="mds-ticket__fm">
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

            <div className="mds-ticket__schedule">
              <Input
                label="schedule in"
                placeholder="1d4h3m"
                value={detail.scheduleInput}
                invalid={scheduleInvalid}
                onChange={(e) => setScheduleInput(e.target.value)}
              />
              <Button
                disabled={!detail.scheduleValid || detail.scheduleInput === ''}
                onClick={() => void schedule()}
              >
                schedule
              </Button>
            </div>

            <textarea
              className="mds-ticket__editor"
              value={body}
              onChange={(e) => setEditedBody(e.target.value)}
              spellCheck={false}
            />
            <div className="mds-ticket__actions">
              <Button
                variant="primary"
                disabled={!dirty || detail.status === 'saving'}
                onClick={() => void saveBody()}
              >
                {detail.status === 'saving' ? 'saving…' : 'save body'}
              </Button>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}
