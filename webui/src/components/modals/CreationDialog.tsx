/** Thin shell: Dialog + Cancel/Submit footer + creation-form + optional error. */

import type { ReactNode } from 'react';
import { Button, Dialog } from '../ds/index.js';

export interface CreationDialogProps {
  readonly title: string;
  readonly onClose: () => void;
  readonly pending: boolean;
  readonly submitLabel: string;
  readonly pendingLabel: string;
  readonly onSubmit: () => void;
  readonly error: string | null;
  readonly children: ReactNode;
  /** Passed through; callers use `open ?? true` for remount-or-compat. */
  readonly open?: boolean;
}

export function CreationDialog({
  title,
  onClose,
  pending,
  submitLabel,
  pendingLabel,
  onSubmit,
  error,
  children,
  open = true,
}: CreationDialogProps): React.JSX.Element {
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onSubmit} disabled={pending}>
            {pending ? pendingLabel : submitLabel}
          </Button>
        </>
      }
    >
      <form
        className="creation-form"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        {children}
        {error !== null ? <p className="mds-field__hint mds-field__hint--error">{error}</p> : null}
      </form>
    </Dialog>
  );
}
