/**
 * HistoryPanel — conversation/intention history over the `history` slice. Loose/all mode toggle
 * filters rows; dismiss and resume actions wire through `history.dismiss` / `history.resumeConversation`.
 */

import { selectHistoryView } from '@murder/ui-core/selectors/historySelectors.js';
import type { HistoryMode } from '@murder/ui-core/selectors/historySelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Panel, ListRow, Tag, Tabs, Button, IconButton, Icon } from '../ds/index.js';
import type { TabItem, TagProps } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

/** Map the view's raw history status onto a DS Tag tone. */
function statusTone(status: string): NonNullable<TagProps['tone']> {
  if (status === 'open') return 'accent';
  if (status === 'stale') return 'brand';
  return 'neutral';
}

export function HistoryPanel(): React.JSX.Element {
  const history = useAppStore((s) => s.history, shallow);
  const dismiss = useAppStore((s) => s.actions.history.dismiss);
  const resume = useAppStore((s) => s.actions.history.resumeConversation);
  const [mode, setMode] = useState<HistoryMode>('loose');
  const view = selectHistoryView(history, mode, Date.now());

  const looseTab = { id: 'loose', label: 'loose', count: view.looseCount || undefined } as TabItem;
  const toggle = (
    <Tabs
      variant="pill"
      value={mode}
      onChange={(id) => setMode(id as HistoryMode)}
      tabs={[looseTab, { id: 'all', label: 'all' }]}
    />
  );

  return (
    <Panel title="history" count={view.isEmpty ? null : view.rows.length} flush actions={toggle} data-panel-id="history">
      <SliceHint state={view} empty="No history." />
      {view.rows.map((row) => (
        <ListRow
          key={row.itemId}
          title={row.text}
          meta={
            <span className="history-meta">
              <span className="history-meta__target">{row.target}</span>
              <span className="history-meta__age">{row.age}</span>
            </span>
          }
          trailing={
            <span className="history-trail">
              <Tag tone={statusTone(row.status)} dot>
                {row.statusTag}
              </Tag>
              {row.resumable ? (
                <Button variant="ghost" size="sm" onClick={() => void resume(row.conversationId)}>
                  resume
                </Button>
              ) : null}
              <IconButton size="sm" label="Dismiss" onClick={() => void dismiss(row.itemId)}>
                <Icon name="x" size={14} />
              </IconButton>
            </span>
          }
        />
      ))}
    </Panel>
  );
}
