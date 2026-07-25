/**
 * NewPlanDialog — web counterpart of inktui's NewPlanModal (`ctrl+p` / command+p).
 * Name + message fields → `createDialogActions(bus).createPlan(...)`.
 */

import { createDialogActions, type CreatePlanInput } from '@core/store/dialogs/dialogActions.js';
import { toastStore } from '@core/store/toast/toastStore.js';
import { useAppStoreApi } from '@core/hooks/useAppStore.js';
import { useCallback, useEffect, useId, useState } from 'react';
import { useApplicationClient } from '../../application/ApplicationClientContext.js';
import { Button, Dialog, Input, Radio } from '../ds/index.js';

export interface NewPlanDialogProps {
  readonly open: boolean;
  readonly onClose: () => void;
}

type Naming = 'auto' | 'custom';

export function NewPlanDialog({ open, onClose }: NewPlanDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const messageId = useId();
  const [message, setMessage] = useState('');
  const [naming, setNaming] = useState<Naming>('auto');
  const [planName, setPlanName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (open) {
      setMessage('');
      setNaming('auto');
      setPlanName('');
      setError(null);
      setPending(false);
    }
  }, [open]);

  const submit = useCallback(() => {
    if (pending) return;
    const autoName = naming === 'auto';
    const trimmedName = planName.trim();
    if (!autoName && trimmedName.length === 0) {
      setError('Plan name is required (or pick "auto").');
      return;
    }
    setPending(true);
    setError(null);
    const body = message;
    const msg = body.trim().length > 0 ? body : undefined;
    const input: CreatePlanInput = autoName
      ? { body, autoName: true, ...(msg !== undefined ? { message: msg } : {}) }
      : {
          body,
          autoName: false,
          planName: trimmedName,
          ...(msg !== undefined ? { message: msg } : {}),
        };
    const actions = createDialogActions(bus);
    void actions
      .createPlan(input)
      .then((result) => {
        onClose();
        toastStore.getState().push(`plan "${result.plan_name}" created`, { ttlMs: 6000 });
        void storeApi.getState().actions.docView.open('plan', result.plan_name);
      })
      .catch((err: unknown) => {
        const text = err instanceof Error ? err.message : String(err);
        setPending(false);
        setError(text);
        toastStore.getState().push(text, { severity: 'error', ttlMs: 12000 });
      });
  }, [bus, message, naming, onClose, pending, planName, storeApi]);

  return (
    <Dialog
      open={open}
      title="New Plan"
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
        <div className="mds-field">
          <label className="mds-field__label" htmlFor={messageId}>
            Message
          </label>
          <textarea
            id={messageId}
            className="mds-input__el creation-form__textarea"
            value={message}
            placeholder="Describe the plan…"
            rows={6}
            disabled={pending}
            autoFocus
            onChange={(e) => {
              setMessage(e.target.value);
              setError(null);
            }}
          />
        </div>

        <div className="mds-field">
          <span className="mds-field__label">Name</span>
          <Radio
            inline
            options={[
              { value: 'auto', label: 'auto' },
              { value: 'custom', label: 'name it myself' },
            ]}
            value={naming}
            disabled={pending}
            onChange={(v) => setNaming(v as Naming)}
          />
        </div>

        {naming === 'custom' ? (
          <Input
            label="Plan name"
            value={planName}
            placeholder="e.g. refactor-auth"
            disabled={pending}
            onChange={(e) => {
              setPlanName(e.target.value);
              setError(null);
            }}
          />
        ) : null}

        {error !== null ? <p className="mds-field__hint mds-field__hint--error">{error}</p> : null}
      </form>
    </Dialog>
  );
}
