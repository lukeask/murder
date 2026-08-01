/** NewPlanDialog — web counterpart of inktui's NewPlanModal (`ctrl+p` / command+p). */

import { prepareCreatePlan, type PlanNaming } from '@murder/ui-core/create/creationPayloads.js';
import { createDialogActions } from '@murder/ui-core/store/dialogs/dialogActions.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useId, useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { Input, Radio } from '../ds/index.js';
import { CreationDialog } from './CreationDialog.js';

export interface NewPlanDialogProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

type Naming = PlanNaming;

export function NewPlanDialog({ open, onClose }: NewPlanDialogProps): React.JSX.Element {
  const bus = useApplicationClient();
  const storeApi = useAppStoreApi();
  const messageId = useId();
  const [message, setMessage] = useState('');
  const [naming, setNaming] = useState<Naming>('auto');
  const [planName, setPlanName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = (): void => {
    if (pending) return;
    const prepared = prepareCreatePlan({ body: message, naming, planName });
    if (!prepared.ok) {
      setError(prepared.error);
      return;
    }
    setPending(true);
    setError(null);
    const actions = createDialogActions(bus);
    void actions
      .createPlan(prepared.value)
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
  };

  return (
    <CreationDialog
      open={open ?? true}
      title="New Plan"
      onClose={onClose}
      pending={pending}
      submitLabel="Create"
      pendingLabel="Creating…"
      onSubmit={submit}
      error={error}
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
    </CreationDialog>
  );
}
