/**
 * DocViewer — renders the open document (plan / note / report) from the `docView` slice. The body is
 * the raw markdown fetched by `docView.open(kind, name)`; we render it in a `<pre>` (no markdown
 * parser dependency — the body is read as plaintext-with-markdown, matching the Ink DocPane which
 * also shows the raw source). Close routes through `docView.close()`.
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';

export function DocViewer(): React.JSX.Element | null {
  const docView = useAppStore((s) => s.docView, shallow);
  const close = useAppStore((s) => s.actions.docView.close);

  if (docView.open === null) {
    return null;
  }

  return (
    <div className="doc-viewer">
      <header className="doc-viewer__head">
        <span className="doc-viewer__title">
          <span className="doc-viewer__kind">{docView.open.kind}</span> {docView.open.name}
        </span>
        <button type="button" className="row-action" onClick={() => close()}>
          close
        </button>
      </header>
      {docView.status === 'loading' ? (
        <p className="panel__hint">Loading…</p>
      ) : docView.status === 'error' ? (
        <p className="panel__hint panel__hint--error">{docView.error ?? 'Failed to load.'}</p>
      ) : (
        <pre className="doc-viewer__body">{docView.body ?? ''}</pre>
      )}
    </div>
  );
}
