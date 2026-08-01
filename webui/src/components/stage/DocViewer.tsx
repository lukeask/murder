/** DocViewer — open plan/note/report from docView; Panel chrome shared with TicketDetail. */

import type { ReactNode } from 'react';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, Tag, IconButton, Icon } from '../ds/index.js';

/** Shared overlay Panel + close + loading/error for DocViewer / TicketDetail. */
export function StageOverlayPanel({
  className,
  title,
  onClose,
  status,
  error,
  children,
}: {
  readonly className: string;
  readonly title: ReactNode;
  readonly onClose: () => void;
  readonly status: string;
  readonly error: string | null;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <div className={`mds-stage-overlay ${className}`}>
      <Panel
        active
        flush
        title={title}
        actions={
          <IconButton label="close" size="md" onClick={onClose}>
            <Icon name="x" />
          </IconButton>
        }
      >
        {status === 'loading' ? (
          <p className="mds-stage__empty">Loading…</p>
        ) : status === 'error' ? (
          <p className="mds-stage__empty">{error ?? 'Failed to load.'}</p>
        ) : (
          children
        )}
      </Panel>
    </div>
  );
}

export function DocViewer(): React.JSX.Element | null {
  const docView = useAppStore((s) => s.docView, shallow);
  const close = useAppStore((s) => s.actions.docView.close);

  if (docView.open === null) return null;

  return (
    <StageOverlayPanel
      className="mds-doc"
      title={
        <span className="mds-stage-overlay__title">
          <Tag tone="accent">{docView.open.kind}</Tag>
          <span className="mds-doc__name">{docView.open.name}</span>
        </span>
      }
      onClose={() => close()}
      status={docView.status}
      error={docView.error}
    >
      <div className="mds-doc__scroll">
        <pre className="mds-doc__body">{docView.body ?? ''}</pre>
      </div>
    </StageOverlayPanel>
  );
}
