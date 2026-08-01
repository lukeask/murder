/** ChatTranscript — chat bubbles via useConversationTurns; stick-to-bottom + goto-line. */

import {
  useConversationTurns,
  type ChatTurn,
  type TurnSpeaker,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useRef } from 'react';
import { Avatar, StatusDot } from '../ds/index.js';
import { GotoLineOverlay } from './GotoLineOverlay.js';
import { useStickToBottom } from './useStickToBottom.js';
import { useWebGotoLine } from './useWebGotoLine.js';

const META_SPEAKERS = new Set<TurnSpeaker>(['notice', 'agent', 'unknown']);
const STRUCT_SPEAKERS = new Set<TurnSpeaker>(['tool', 'plan']);

const DELIVERY_LABEL: Partial<Record<NonNullable<ChatTurn['delivery']>, string>> = {
  sending: 'sending…',
  accepted: 'accepted',
  queued: 'queued',
  failed: 'failed',
  unknown: 'unconfirmed',
};

const DELIVERY_CLASS: Partial<Record<NonNullable<ChatTurn['delivery']>, string>> = {
  failed: 'mds-bubble--delivery-failed',
  unknown: 'mds-bubble--delivery-unknown',
};

export function ChatTranscript({ agentId }: { readonly agentId: string }): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const defaultChatViewMode = useAppStore((s) => s.settings.defaultChatViewMode);
  const stored = conversations.paneViewModes[agentId] ?? defaultChatViewMode;
  // Terminal tab owns tmux; chat pane only renders verbose/condensed.
  const viewMode = stored === 'tmux' ? 'verbose' : stored;
  const turns = useConversationTurns(agentId, conversations, viewMode);
  const threadRef = useRef<HTMLDivElement>(null);
  const turnRefs = useRef<(HTMLDivElement | null)[]>([]);

  const { onScroll } = useStickToBottom(threadRef, turns.length);

  const jump = useCallback((line: number) => {
    const count = turnRefs.current.length;
    if (count === 0) return;
    const index = Math.min(Math.max(line, 1), count) - 1;
    turnRefs.current[index]?.scrollIntoView({ block: 'start' });
  }, []);
  const goto = useWebGotoLine(jump, true);

  if (turns.length === 0) {
    return <div className="mds-stage__empty">No messages yet.</div>;
  }

  return (
    <div className="mds-thread-wrap">
      <div className="mds-thread" ref={threadRef} onScroll={onScroll}>
        {turns.map((turn, i) => (
          <div
            key={turn.blockId ?? `t${i}`}
            ref={(el) => {
              turnRefs.current[i] = el;
            }}
            data-turn-line={i + 1}
          >
            <Turn turn={turn} agentId={agentId} />
          </div>
        ))}
      </div>
      <GotoLineOverlay pending={goto.pending} />
    </div>
  );
}

function Turn({ turn, agentId }: { readonly turn: ChatTurn; readonly agentId: string }): React.JSX.Element {
  if (turn.isLivePrompt === true) {
    return (
      <div className="mds-work">
        <StatusDot status="running" pulse label="running" />
      </div>
    );
  }

  if (META_SPEAKERS.has(turn.speaker)) {
    return (
      <div className="mds-msg mds-msg--meta">
        <span className="mds-meta-chip">{turn.text}</span>
      </div>
    );
  }

  const isUser = turn.speaker === 'user';
  const struct = STRUCT_SPEAKERS.has(turn.speaker);
  const delivery = turn.delivery;
  const deliveryClass =
    delivery === undefined
      ? ''
      : ` ${DELIVERY_CLASS[delivery] ?? 'mds-bubble--delivery-pending'}`;
  const label = delivery === undefined ? null : (DELIVERY_LABEL[delivery] ?? null);

  return (
    <div className={`mds-msg ${isUser ? 'mds-msg--user' : 'mds-msg--crow'}`}>
      {isUser ? null : <Avatar name={agentId} size="md" />}
      <div className="mds-msg__col">
        {isUser ? null : <span className="mds-msg__role">{agentId}</span>}
        <div
          className={`mds-bubble ${isUser ? 'mds-bubble--user' : 'mds-bubble--crow'}${
            struct ? ' mds-bubble--struct' : ''
          }${deliveryClass}`}
        >
          {turn.text}
          {label === null ? null : <span className="mds-bubble__delivery">{label}</span>}
        </div>
      </div>
    </div>
  );
}
