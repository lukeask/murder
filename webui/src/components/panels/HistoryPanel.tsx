/**
 * HistoryPanel — conversation/intention history over the `history` slice. Loose/all mode toggle
 * filters rows; dismiss and resume actions wire through `history.dismiss` / `history.resumeConversation`.
 * Keyboard (when focused): j/k, Enter/r resume, x dismiss, a loose↔all.
 */

import { selectHistoryView } from '@murder/ui-core/selectors/historySelectors.js';
import type { HistoryMode } from '@murder/ui-core/selectors/historySelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { usePaneHistoryMode } from '../../composer/usePaneHistoryMode.js';
import { usePaneUiClampedCursor } from '../../composer/usePaneUiClampedCursor.js';
import { panelFocusStore, useIsPanelFocused } from '../../panelFocus.js';
import { usePanelListKeys } from '../../usePanelListKeys.js';
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
  const refresh = useAppStore((s) => s.actions.history.refresh);
  const [mode, setMode] = usePaneHistoryMode('history');
  const view = selectHistoryView(history, mode, Date.now());
  const focused = useIsPanelFocused('history');
  const [cursor, setCursor] = usePaneUiClampedCursor('history', view.rows.length);

  const looseTab = { id: 'loose', label: 'loose', count: view.looseCount || undefined } as TabItem;
  const toggle = (
    <Tabs
      variant="pill"
      value={mode}
      onChange={(id) => setMode(id as HistoryMode)}
      tabs={[looseTab, { id: 'all', label: 'all' }]}
    />
  );

  usePanelListKeys({
    active: focused,
    itemCount: view.rows.length,
    cursor,
    setCursor,
    onActivate: () => {
      const row = view.rows[cursor];
      if (row?.resumable === true) void resume(row.conversationId);
    },
    onAction: (key) => {
      if (key === 'a') {
        setMode((m) => (m === 'loose' ? 'all' : 'loose'));
        return true;
      }
      const row = view.rows[cursor];
      if (row === undefined) {
        if (key === 'r') {
          void refresh();
          return true;
        }
        return false;
      }
      if (key === 'r') {
        if (row.resumable) void resume(row.conversationId);
        else void refresh();
        return true;
      }
      if (key === 'x') {
        void dismiss(row.itemId);
        return true;
      }
      return false;
    },
  });

  return (
    <Panel
      title="history"
      count={view.isEmpty ? null : view.rows.length}
      flush
      active={focused}
      actions={toggle}
      data-panel-id="history"
      onHeaderClick={() => panelFocusStore.getState().focus('history')}
    >
      <SliceHint state={view} empty="No history." />
      {view.rows.map((row, index) => (
        <ListRow
          key={row.itemId}
          selected={focused && index === cursor}
          onClick={() => {
            panelFocusStore.getState().focus('history');
            setCursor(index);
            if (row.resumable) void resume(row.conversationId);
          }}
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
