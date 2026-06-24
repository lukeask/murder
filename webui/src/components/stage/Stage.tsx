/**
 * Stage — the center region. Hosts the focused agent's conversation (transcript + composer) plus the
 * "watch the terminal" tmux frame view, and — when one is open — the doc viewer or ticket detail
 * layered on top (closing them returns to the chat, mirroring the Ink Stage's pane model).
 *
 * Reskinned onto the DS: the chat/terminal switch is a DS {@link Tabs} (underline). Data wiring is
 * UNCHANGED (rule 2): the active chat agent comes from {@link selectActiveAgentId}; the doc/ticket
 * overlay priority (ticket open → TicketDetail; doc open → DocViewer; else chat/terminal tabs) is
 * preserved exactly.
 */

import { selectActiveAgentId } from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
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

  // A doc / ticket takes over the Stage when open (an explicit overlay the user closes); otherwise
  // the chat/terminal for the active agent.
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
          <TmuxFrameView agentId={agentId} />
        )}
      </div>
      <ChatInput />
    </div>
  );
}
