/** Stage — center column: chat/terminal tabs, or doc/ticket overlay when open. */

import { selectActiveAgentId } from '@murder/ui-core/selectors/conversationsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Tabs } from '../ds/index.js';
import { ChatTranscript } from './ChatTranscript.js';
import { ChatInput } from './ChatInput.js';
import { TmuxFrameView } from './TmuxFrameView.js';
import { DocViewer } from './DocViewer.js';
import { TicketDetail } from './TicketDetail.js';

type StageTab = 'chat' | 'terminal';

export function Stage(): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const docOpen = useAppStore((s) => s.docView.open !== null);
  const ticketOpen = useAppStore((s) => s.ticketDetail.ticketId !== null);
  const [tab, setTab] = useState<StageTab>('chat');

  const agentId = selectActiveAgentId(conversations, roster, favorites);
  // Terminal attach needs a real session UUID — never fall back to agentId.
  const terminalSessionId =
    agentId === null
      ? null
      : (roster.rows.find((row) => row.agentId === agentId)?.sessionId ?? null);

  if (ticketOpen) {
    return (
      <div className="stage mds-stage mds-stage--overlay">
        <TicketDetail />
      </div>
    );
  }
  if (docOpen) {
    return (
      <div className="stage mds-stage mds-stage--overlay">
        <DocViewer />
      </div>
    );
  }

  return (
    <div className="stage mds-stage">
      <div className="mds-stage__tabs">
        <Tabs
          tabs={[
            { id: 'chat', label: 'Chat' },
            { id: 'terminal', label: 'Terminal' },
          ]}
          value={tab}
          onChange={(id) => setTab(id as StageTab)}
        />
        {agentId !== null ? (
          <span className="mds-stage__target">
            <span className="star">★</span>
            {agentId}
          </span>
        ) : null}
      </div>
      <div className="mds-stage__body">
        {agentId === null ? (
          <div className="mds-stage__empty">
            {tab === 'terminal'
              ? 'Select a crow from the roster to watch its terminal.'
              : 'Select a crow from the roster to start chatting.'}
          </div>
        ) : tab === 'chat' ? (
          <ChatTranscript agentId={agentId} />
        ) : (
          <TmuxFrameView sessionId={terminalSessionId} />
        )}
      </div>
      <ChatInput />
    </div>
  );
}
