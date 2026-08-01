/** NewReportDialog — web counterpart of inktui's NewReportModal (panel “+” / shared entrypoint). */

import { createDialogActions } from '@murder/ui-core/store/dialogs/dialogActions.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useState } from 'react';
import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { Input } from '../ds/index.js';
import { CreationDialog } from './CreationDialog.js';

export interface NewReportDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

export function NewReportDialog({ open, onClose }: NewReportDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = (): void => {
    const trimmed = name.trim();
    if (trimmed === '') {
      setError('name required');
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    const actions = createDialogActions(bus);
    void actions
      .createReport(trimmed)
      .then((result) => {
        onClose();
        toastStore.getState().push(`report "${result.name}" created`, { ttlMs: 6000 });
        void storeApi.getState().actions.docView.open('report', result.name);
        void storeApi.getState().actions.reports.refresh();
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
      title="New report"
      onClose={onClose}
      pending={pending}
      submitLabel="Create"
      pendingLabel="Creating…"
      onSubmit={submit}
      error={error}
    >
      <Input
        label="Name"
        value={name}
        placeholder="report-name"
        autoFocus
        disabled={pending}
        onChange={(e) => {
          setName(e.target.value);
          setError(null);
        }}
      />
    </CreationDialog>
  );
}
