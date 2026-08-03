/** TicketDetail — open ticket frontmatter + body editor + schedule; uses StageOverlayPanel. */

import type { ReactNode } from 'react';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Tag, Input, Button, Checkbox } from '../ds/index.js';
import { StageOverlayPanel } from './DocViewer.js';

/** Toggle a checklist line: `- [ ] text` ↔ `- [x] text`. Non-checklist lines unchanged. */
function toggleChecklist(line: string): string {
  const unchecked = line.replace(/^(\s*-\s*)\[ \]/, '$1[x]');
  if (unchecked !== line) return unchecked;
  const checked = line.replace(/^(\s*-\s*)\[x\]/, '$1[ ]');
  return checked !== line ? checked : line;
}

/** Is a line a checklist item (either checked or unchecked)? */
function isChecklistLine(line: string): boolean {
  return /^\s*-\s*\[[x ]\]/.test(line);
}

function checklistLabel(line: string): string {
  return line.replace(/^\s*-\s*\[[x ]\]\s*/, '');
}

export function TicketDetail({
  layoutActions,
}: {
  /** Stage layout controls (expand / pop-beside) rendered in the overlay header. */
  readonly layoutActions?: ReactNode;
} = {}): React.JSX.Element | null {
  const detail = useAppStore((s) => s.ticketDetail, shallow);
  const setEditedBody = useAppStore((s) => s.actions.ticketDetail.setEditedBody);
  const setScheduleInput = useAppStore((s) => s.actions.ticketDetail.setScheduleInput);
  const saveBody = useAppStore((s) => s.actions.ticketDetail.saveBody);
  const schedule = useAppStore((s) => s.actions.ticketDetail.schedule);
  const close = useAppStore((s) => s.actions.ticketDetail.close);

  if (detail.ticketId === null) return null;

  const fm = detail.frontmatter;
  const body = detail.editedBody ?? detail.savedBody ?? '';
  const scheduleInvalid = detail.scheduleInput !== '' && !detail.scheduleValid;
  const scheduleAt = fm?.scheduleAt ?? null;
  const lines = body.split('\n');
  const checklistIndices = lines
    .map((line, index) => (isChecklistLine(line) ? index : -1))
    .filter((i) => i >= 0);

  const toggleLine = (index: number): void => {
    const next = lines.map((line, i) => (i === index ? toggleChecklist(line) : line));
    setEditedBody(next.join('\n'));
  };

  /** Match TUI `w`: save body + schedule (if valid) then close overlay. */
  const saveAndClose = (): void => {
    void saveBody();
    void schedule();
    close();
  };

  return (
    <StageOverlayPanel
      className="mds-ticket"
      title={
        <span className="mds-stage-overlay__title">
          <Tag tone="accent">work</Tag>
          <span>{detail.ticketId}</span>
        </span>
      }
      onClose={() => close()}
      status={detail.status}
      error={detail.error}
      actions={layoutActions}
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
          <dt>scheduled</dt>
          <dd>{scheduleAt ?? '(none)'}</dd>
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

      {checklistIndices.length > 0 ? (
        <ul className="mds-ticket__checklist">
          {checklistIndices.map((index) => {
            const line = lines[index] ?? '';
            const checked = /\[x\]/.test(line);
            return (
              <li key={index} className="mds-ticket__checklist-item">
                <Checkbox
                  checked={checked}
                  label={checklistLabel(line) || '(empty)'}
                  onChange={() => toggleLine(index)}
                />
              </li>
            );
          })}
        </ul>
      ) : null}

      <textarea
        className="mds-ticket__editor"
        value={body}
        onChange={(e) => setEditedBody(e.target.value)}
        spellCheck={false}
      />
      <div className="mds-ticket__actions">
        <Button
          variant="primary"
          disabled={detail.status === 'saving'}
          onClick={saveAndClose}
        >
          {detail.status === 'saving' ? 'saving…' : 'save'}
        </Button>
      </div>
    </StageOverlayPanel>
  );
}
