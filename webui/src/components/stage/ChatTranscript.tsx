/**
 * ChatTranscript — the readable chat history for one agent, reskinned onto the DS as real chat bubbles
 * (crow-chat template / chat.jsx). Data wiring is UNCHANGED: maps the `conversations` slice via
 * {@link selectConversationTurns} (display-ready {@link ChatTurn}s); the component only lays out the
 * bubbles by `speaker` and keeps the auto-scroll-on-new-turn affordance (rule 2 — no formatting here).
 *
 * Speaker → presentation map:
 *  - user                : right-aligned accent-tinted bubble (row-reverse), bottom-right tucked.
 *  - assistant           : left-aligned raised crow bubble + Avatar, bottom-left tucked + role label.
 *  - tool / plan         : left crow bubble, denser mono "struct" type (tool output / plan checklist).
 *  - prompt              : when LIVE (trailing unanswered choice_prompt) → a StatusDot pulse "running"
 *                          working line (answered from the composer); otherwise a crow bubble.
 *  - notice / agent      : centered muted mono pill chip (system / lifecycle notices).
 *  - unknown             : centered muted chip (pass-through fallback).
 */

import {
  selectConversationTurns,
  type ChatTurn,
  type TurnSpeaker,
} from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { useEffect, useRef } from 'react';
import { Avatar, StatusDot } from '../ds/index.js';

/** Centered muted chips: lifecycle/system speakers that aren't a back-and-forth message. */
const META_SPEAKERS = new Set<TurnSpeaker>(['notice', 'agent', 'unknown']);
/** Left crow bubbles rendered in the denser monospace "struct" type. */
const STRUCT_SPEAKERS = new Set<TurnSpeaker>(['tool', 'plan']);

export function ChatTranscript({ agentId }: { readonly agentId: string }): React.JSX.Element {
  const blocks = useAppStore((s) => s.conversations.transcripts[agentId]);
  const turns = selectConversationTurns(blocks);
  const endRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to the newest turn whenever the transcript grows (the natural chat affordance).
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
  // A live (trailing, unanswered) choice prompt is the "working" state: a soft pulse dot + label.
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

  return (
    <div className={`mds-msg ${isUser ? 'mds-msg--user' : 'mds-msg--crow'}`}>
      {isUser ? null : <Avatar name={agentId} size="md" />}
      <div className="mds-msg__col">
        {isUser ? null : <span className="mds-msg__role">{agentId}</span>}
        <div
          className={`mds-bubble ${isUser ? 'mds-bubble--user' : 'mds-bubble--crow'}${
            struct ? ' mds-bubble--struct' : ''
          }`}
        >
          {turn.text}
        </div>
      </div>
    </div>
  );
}
