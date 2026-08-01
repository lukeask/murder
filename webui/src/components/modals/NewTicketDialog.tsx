/** NewTicketDialog — web counterpart of inktui's NewTicketModal (`ctrl+t`). */

import { createDialogActions } from '@murder/ui-core/store/dialogs/dialogActions.js';
import { canLaunchBuiltinTicket } from '@murder/ui-core/store/dialogs/canLaunchBuiltinTicket.js';
import { prepareTicketTitle } from '@murder/ui-core/create/creationPayloads.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useId, useState } from 'react';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { shallow } from 'zustand/shallow';
import { Input } from '../ds/index.js';
import { CreationDialog } from './CreationDialog.js';

export interface NewTicketDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

export function NewTicketDialog({ open, onClose }: NewTicketDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const settings = useAppStore((s) => s.settings, shallow);
  const preferBuiltin = canLaunchBuiltinTicket(settings);
  const promptId = useId();
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = (): void => {
    const preparedTitle = prepareTicketTitle(title);
    if (!preparedTitle.ok) {
      setError(preparedTitle.error);
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    const actions = createDialogActions(bus);
    const create = preferBuiltin
      ? actions.startBuiltinTicket({ title: preparedTitle.value, prompt }).then((result) => ({
          ticket_id: result.ticket_id,
          title: result.title,
        }))
      : actions.quickCreateTicket(preparedTitle.value).then((result) => ({
          ticket_id: result.ticket_id,
          title: result.title,
        }));
    void create
      .then((result) => {
        onClose();
        toastStore
          .getState()
          .push(
            preferBuiltin
              ? `ticket "${result.title}" started`
              : `ticket "${result.title}" created`,
            { ttlMs: 6000 },
          );
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        setPending(false);
        setError(message);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  };

  return (
    <CreationDialog
      open={open ?? true}
      title="New Ticket"
      onClose={onClose}
      pending={pending}
      submitLabel={preferBuiltin ? 'Start' : 'Create'}
      pendingLabel={preferBuiltin ? 'Starting…' : 'Creating…'}
      onSubmit={submit}
      error={error}
    >
      <Input
        label="Title"
        value={title}
        placeholder="Short description of the work…"
        autoFocus
        disabled={pending}
        onChange={(e) => {
          setTitle(e.target.value);
          setError(null);
        }}
      />
      {preferBuiltin ? (
        <div className="mds-field">
          <label className="mds-field__label" htmlFor={promptId}>
            Instructions
          </label>
          <textarea
            id={promptId}
            className="mds-input__el creation-form__textarea"
            value={prompt}
            placeholder="Optional brief for the agent…"
            rows={4}
            disabled={pending}
            onChange={(e) => {
              setPrompt(e.target.value);
              setError(null);
            }}
          />
        </div>
      ) : null}
    </CreationDialog>
  );
}
