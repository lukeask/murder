/**
 * Stage — the center region. Hosts the focused agent's conversation (transcript + chat input) plus
 * the "watch the terminal" tmux frame view, and — when one is open — the doc viewer or ticket
 * detail layered on top (closing them returns to the chat, mirroring the Ink Stage's pane model).
 *
 * The active chat agent comes from {@link selectActiveAgentId} (conversations + roster + favorites);
 * a local Chat/Terminal tab toggles the transcript vs. the live tmux frame for that agent.
 */

import { selectActiveAgentId } from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
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
      <div className="stage">
        <TicketDetail />
      </div>
    );
  }
  if (docOpen) {
    return (
      <div className="stage">
        <DocViewer />
      </div>
    );
  }

  return (
    <div className="stage">
      <div className="stage__tabs">
        <button type="button" data-on={tab === 'chat'} onClick={() => setTab('chat')}>
          Chat
        </button>
        <button
          type="button"
          data-on={tab === 'terminal'}
          disabled={agentId === null}
          onClick={() => setTab('terminal')}
        >
          Terminal
        </button>
        {agentId !== null ? <span className="stage__target">{agentId}</span> : null}
      </div>
      <div className="stage__body">
        {agentId === null ? (
          <div className="chat__empty">Select a crow from the roster to start chatting.</div>
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
