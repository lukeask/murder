/**
 * MurderConfirmDialog — confirm UI for an armed {@link murderConfirmStore} target.
 * Confirm runs `agent.stop` (same path as the TUI murder chord); cancel / expiry clears pending.
 */

import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { submitCommand } from '@murder/ui-core/store/commandSubmit.js';
import { murderConfirmStore } from '@murder/ui-core/store/murder/murderConfirmStore.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useCallback, useEffect } from 'react';
import { useStore } from 'zustand';
import { Button, Dialog } from '../ds/index.js';

export function MurderConfirmDialog(): React.JSX.Element | null {
  const bus = useApplicationClient();
  const pending = useStore(murderConfirmStore, (s) => s.pending);

  const clear = useCallback(() => {
    murderConfirmStore.getState().clear();
  }, []);

  const confirm = useCallback(() => {
    const target = murderConfirmStore.getState().pending;
    murderConfirmStore.getState().clear();
    if (target === null) return;
    void submitCommand(bus, 'agent.stop', { agent_id: target.agentId })
      .then(() => {
        toastStore.getState().push(`murdered ${target.name}`, { ttlMs: 6000 });
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  }, [bus]);

  // Second-press confirm: bare `m` while armed (TUI parity), outside typing fields.
  useEffect(() => {
    if (pending === null) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.repeat) return;
      const t = e.target;
      if (
        t instanceof HTMLElement &&
        (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      ) {
        return;
      }
      if (e.key === 'm' || e.key === 'M') {
        e.preventDefault();
        confirm();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [pending, confirm]);

  if (pending === null) return null;

  return (
    <Dialog
      open
      title="Murder crow"
      onClose={clear}
      className="murder-confirm-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={clear}>
            Cancel
          </Button>
          <Button variant="brand" onClick={confirm}>
            Murder {pending.name}
          </Button>
        </>
      }
    >
      <p>
        Kill <strong>{pending.name}</strong>? Press <kbd>m</kbd> again or confirm below.
      </p>
    </Dialog>
  );
}
