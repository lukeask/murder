/**
 * HistoryPanel — the conversation/intention history over the `history` slice via
 * {@link selectHistoryView}. A loose/all mode toggle (local state) controls the filter. Rows can be
 * dismissed (`history.dismiss`) and resumable rows resumed (`history.resumeConversation`).
 *
 * Reskinned onto the DS (C2, follows the TicketsPanel exemplar): a DS {@link Panel} wraps one
 * {@link ListRow} per history item — the intention text is the title, the target crow + relative age
 * is the mono meta line, and the status is a trailing {@link Tag} (tone derived from the view's raw
 * status). Resume (when resumable) + dismiss are small ghost controls in the trailing slot. The
 * loose/all segmented filter rides the Panel's `actions` slot as DS {@link Tabs} (pill variant),
 * preserving the existing `mode` state + `selectHistoryView(history, mode, Date.now())` wiring.
 */

import { selectHistoryView } from '@core/selectors/historySelectors.js';
import type { HistoryMode } from '@core/selectors/historySelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Panel, ListRow, Tag, Tabs, Button, IconButton, Icon } from '../ds/index.js';
import type { TabItem, TagProps } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

/** Map the view's raw history status onto a DS Tag tone (rule 2: tone derived from the view, not
 * re-string-matched for meaning beyond this presentation map). */
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

  const looseTab: TabItem =
    view.looseCount > 0
      ? { id: 'loose', label: 'loose', count: view.looseCount }
      : { id: 'loose', label: 'loose' };
  const toggle = (
    <Tabs
      variant="pill"
      value={mode}
      onChange={(id) => setMode(id as HistoryMode)}
      tabs={[looseTab, { id: 'all', label: 'all' }]}
    />
  );

  return (
    <Panel title="History" count={view.isEmpty ? null : view.rows.length} flush actions={toggle}>
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
