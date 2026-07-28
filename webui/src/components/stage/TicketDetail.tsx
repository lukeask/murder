/** TicketDetail — open ticket frontmatter + body editor + schedule; uses StageOverlayPanel. */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Tag, Input, Button } from '../ds/index.js';
import { StageOverlayPanel } from './DocViewer.js';

export function TicketDetail(): React.JSX.Element | null {
  const detail = useAppStore((s) => s.ticketDetail, shallow);
  const setEditedBody = useAppStore((s) => s.actions.ticketDetail.setEditedBody);
  const setScheduleInput = useAppStore((s) => s.actions.ticketDetail.setScheduleInput);
  const saveBody = useAppStore((s) => s.actions.ticketDetail.saveBody);
  const schedule = useAppStore((s) => s.actions.ticketDetail.schedule);
  const close = useAppStore((s) => s.actions.ticketDetail.close);

  if (detail.ticketId === null) return null;

  const fm = detail.frontmatter;
  const body = detail.editedBody ?? detail.savedBody ?? '';
  const dirty = detail.editedBody !== null && detail.editedBody !== detail.savedBody;
  const scheduleInvalid = detail.scheduleInput !== '' && !detail.scheduleValid;

  return (
    <StageOverlayPanel
      className="mds-ticket"
      title={
        <span className="mds-stage-overlay__title">
          <Tag tone="accent">ticket</Tag>
          <span>{detail.ticketId}</span>
        </span>
      }
      onClose={() => close()}
      status={detail.status}
      error={detail.error}
    >
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
    </StageOverlayPanel>
  );
}
