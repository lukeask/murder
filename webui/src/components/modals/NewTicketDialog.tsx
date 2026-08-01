/** NewTicketDialog — web counterpart of inktui's NewTicketModal (`ctrl+t`). */

import { createDialogActions } from '@murder/ui-core/store/dialogs/dialogActions.js';
import { prepareTicketTitle } from '@murder/ui-core/create/creationPayloads.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { Input } from '../ds/index.js';
import { CreationDialog } from './CreationDialog.js';

export interface NewTicketDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

export function NewTicketDialog({ open, onClose }: NewTicketDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const [title, setTitle] = useState('');
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
    void actions
      .quickCreateTicket(preparedTitle.value)
      .then((result) => {
        onClose();
        toastStore.getState().push(`ticket "${result.title}" created`, { ttlMs: 6000 });
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
      submitLabel="Create"
      pendingLabel="Creating…"
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
    </CreationDialog>
  );
}
