/**
 * DocViewer — the open document (plan / note / report) from the `docView` slice, reskinned onto the DS
 * plan-viewer template: an `active`, `flush` DS Panel whose title carries the kind Tag + name and whose
 * header actions hold a close IconButton; the body scrolls the fetched markdown in a <pre> with the
 * doc typography (defined in panels-stage.css, referencing DS tokens). Data wiring is UNCHANGED (rule 2):
 * `docView` body/status/error + the `docView.close()` action; still rendered as plaintext-with-markdown
 * (no markdown parser — matches the Ink DocPane raw source).
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, Tag, IconButton, Icon } from '../ds/index.js';

export function DocViewer(): React.JSX.Element | null {
  const docView = useAppStore((s) => s.docView, shallow);
  const close = useAppStore((s) => s.actions.docView.close);

  if (docView.open === null) {
    return null;
  }

  return (
    <div className="mds-doc">
      <Panel
        active
        flush
        title={
          <span className="mds-doc__title">
            <Tag tone="accent">{docView.open.kind}</Tag>
            <span className="mds-doc__name">{docView.open.name}</span>
          </span>
        }
        actions={
          <IconButton label="close" size="md" onClick={() => close()}>
            <Icon name="x" />
          </IconButton>
        }
      >
        {docView.status === 'loading' ? (
          <p className="mds-stage__empty">Loading…</p>
        ) : docView.status === 'error' ? (
          <p className="mds-stage__empty">{docView.error ?? 'Failed to load.'}</p>
        ) : (
          <div className="mds-doc__scroll">
            <pre className="mds-doc__body">{docView.body ?? ''}</pre>
          </div>
        )}
      </Panel>
    </div>
  );
}
