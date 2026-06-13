/**
 * HistoryPanel — the conversation/intention history over the `history` slice via
 * {@link selectHistoryView}. A loose/all mode toggle (local state) controls the filter. Rows can be
 * dismissed (`history.dismiss`) and resumable rows resumed (`history.resumeConversation`).
 */

import { selectHistoryView } from '@core/selectors/historySelectors.js';
import type { HistoryMode } from '@core/selectors/historySelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Panel } from '../Panel.js';
import { SliceHint } from '../SliceHint.js';

export function HistoryPanel(): React.JSX.Element {
  const history = useAppStore((s) => s.history, shallow);
  const dismiss = useAppStore((s) => s.actions.history.dismiss);
  const resume = useAppStore((s) => s.actions.history.resumeConversation);
  const [mode, setMode] = useState<HistoryMode>('loose');
  const view = selectHistoryView(history, mode, Date.now());

  const toggle = (
    <div className="seg">
      <button type="button" data-on={mode === 'loose'} onClick={() => setMode('loose')}>
        loose{view.looseCount > 0 ? ` (${view.looseCount})` : ''}
      </button>
      <button type="button" data-on={mode === 'all'} onClick={() => setMode('all')}>
        all
      </button>
    </div>
  );

  return (
    <Panel title="History" actions={toggle}>
      <SliceHint state={view} empty="No history." />
      <ul className="list">
        {view.rows.map((row) => (
          <li key={row.itemId} className="list__row history__row">
            <span className={`tag tag--${row.status}`}>{row.statusTag}</span>
            <span className="list__primary history__text">{row.text}</span>
            <span className="history__target">{row.target}</span>
            <span className="history__age">{row.age}</span>
            {row.resumable ? (
              <button
                type="button"
                className="row-action"
                title="Resume conversation"
                onClick={() => void resume(row.target)}
              >
                resume
              </button>
            ) : null}
            <button
              type="button"
              className="row-action"
              title="Dismiss"
              onClick={() => void dismiss(row.itemId)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
