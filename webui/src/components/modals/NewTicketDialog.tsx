/**
 * NewTicketDialog — web counterpart of inktui's NewTicketModal (`ctrl+t`).
 * Title field → `createDialogActions(bus).quickCreateTicket(title)`.
 */

import { createDialogActions } from '@core/store/dialogs/dialogActions.js';
import { toastStore } from '@core/store/toast/toastStore.js';
import { useCallback, useEffect, useState } from 'react';
import { useApplicationClient } from '../../application/ApplicationClientContext.js';
import { Button, Dialog, Input } from '../ds/index.js';

export interface NewTicketDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
}

export function NewTicketDialog({ open, onClose }: NewTicketDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const [title, setTitle] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (open) {
      setTitle('');
      setError(null);
      setPending(false);
    }
  }, [open]);

  const submit = useCallback(() => {
    const trimmed = title.trim();
    if (trimmed.length === 0) {
      setError('Ticket title is required.');
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    const actions = createDialogActions(bus);
    void actions
      .quickCreateTicket(trimmed)
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
  }, [bus, onClose, pending, title]);

  return (
    <Dialog
      open={open}
      title="New Ticket"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={pending}>
            {pending ? 'Creating…' : 'Create'}
          </Button>
        </>
      }
    >
      <form
        className="creation-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Input
          label="Title"
          value={title}
          placeholder="Short description of the work…"
          autoFocus
          invalid={error !== null}
          {...(error !== null ? { hint: error } : {})}
          disabled={pending}
          onChange={(e) => {
            setTitle(e.target.value);
            setError(null);
          }}
        />
      </form>
    </Dialog>
  );
}
