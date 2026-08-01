/** ChatTranscript — chat bubbles from selectMergedConversationTurns; auto-scrolls on growth. */

import {
  selectMergedConversationTurns,
  type ChatTurn,
  type TurnSpeaker,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { useEffect, useRef } from 'react';
import { Avatar, StatusDot } from '../ds/index.js';

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
  const blocks = useAppStore((s) => s.conversations.transcripts[agentId]);
  const pending = useAppStore((s) => s.conversations.pendingByAgent[agentId]);
  const turns = selectMergedConversationTurns(blocks, pending);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [turns.length]);

  if (turns.length === 0) {
    return <div className="mds-stage__empty">No messages yet.</div>;
  }

  return (
    <div className="mds-thread">
      {turns.map((turn, i) => (
        <Turn key={turn.blockId ?? `t${i}`} turn={turn} agentId={agentId} />
      ))}
      <div ref={endRef} />
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
