/**
 * NoteCaptureDialog — web counterpart of inktui's noteCaptureMode (`ctrl+n` / notes panel “+”).
 * Draft + optional title; submit via `notetaker.capture.submit`. Draft persists across cancel.
 */

import { submitCommand } from '@murder/ui-core/store/commandSubmit.js';
import { noteCaptureStore } from '@murder/ui-core/store/notes/noteCaptureStore.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useId, useState, useSyncExternalStore } from 'react';
import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { Input } from '../ds/index.js';
import { CreationDialog } from './CreationDialog.js';

export interface NoteCaptureDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

export function NoteCaptureDialog({ open, onClose }: NoteCaptureDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const draft = useSyncExternalStore(
    noteCaptureStore.subscribe,
    () => noteCaptureStore.getState().draftText,
    () => noteCaptureStore.getState().draftText,
  );
  const title = useSyncExternalStore(
    noteCaptureStore.subscribe,
    () => noteCaptureStore.getState().titleText,
    () => noteCaptureStore.getState().titleText,
  );
  const draftId = useId();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const closeWithoutReset = (): void => {
    // Cancel keeps draft/title for reopen (same as TUI noteCaptureMode Escape).
    onClose();
  };

  const submit = (): void => {
    if (draft.trim() === '') {
      setError('note required');
      return;
    }
    if (pending) return;
    setPending(true);
    setError(null);
    const raw = draft;
    const titleTrimmed = title.trim();
    // Reset only on confirmed submit so the next capture starts empty.
    noteCaptureStore.getState().reset();
    onClose();
    toastStore.getState().push('note captured', { ttlMs: 6000 });
    void submitCommand(bus, 'notetaker.capture.submit', {
      raw,
      ...(titleTrimmed !== '' ? { title: titleTrimmed } : {}),
    })
      .then(() => {
        void storeApi.getState().actions.notes.refresh();
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  };

  return (
    <CreationDialog
      open={open ?? true}
      title="Quick note"
      onClose={closeWithoutReset}
      pending={pending}
      submitLabel="Save"
      pendingLabel="Saving…"
      onSubmit={submit}
      error={error}
    >
      <Input
        label="Title (optional)"
        value={title}
        placeholder="leave empty to auto-title"
        disabled={pending}
        onChange={(e) => {
          noteCaptureStore.getState().setTitle(e.target.value);
          setError(null);
        }}
      />
      <div className="mds-field">
        <label className="mds-field__label" htmlFor={draftId}>
          Note
        </label>
        <textarea
          id={draftId}
          className="mds-input__el creation-form__textarea"
          value={draft}
          placeholder="capture a thought…"
          rows={6}
          disabled={pending}
          autoFocus
          onChange={(e) => {
            noteCaptureStore.getState().setDraft(e.target.value);
            setError(null);
          }}
        />
      </div>
    </CreationDialog>
  );
}
