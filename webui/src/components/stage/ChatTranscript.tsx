/**
 * ChatTranscript — the readable chat history for one agent. Maps to the `conversations` slice via
 * {@link selectConversationTurns}, which yields display-ready {@link ChatTurn}s (speaker + text). The
 * component only colours by `speaker` (a CSS class) and lays the bubbles out; all formatting (tool
 * call rendering, plan checklists, choice prompts) is done by the selector (rule 2).
 *
 * A trailing live `choice_prompt` (`turn.isLivePrompt`) is highlighted as an open dialog — the user
 * answers it from the ChatInput (keys forward to the agent's pane via `conversations.sendKey`).
 */

import { selectConversationTurns } from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { useEffect, useRef } from 'react';

export function ChatTranscript({ agentId }: { readonly agentId: string }): React.JSX.Element {
  const blocks = useAppStore((s) => s.conversations.transcripts[agentId]);
  const turns = selectConversationTurns(blocks);
  const endRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to the newest turn whenever the transcript grows (the natural chat affordance).
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [turns.length]);

  if (turns.length === 0) {
    return <div className="chat__empty">No messages yet.</div>;
  }

  return (
    <div className="chat__transcript">
      {turns.map((turn, i) => (
        <div
          key={turn.blockId ?? `t${i}`}
          className={`turn turn--${turn.speaker}${turn.isLivePrompt === true ? ' turn--live' : ''}`}
        >
          <span className="turn__speaker">{turn.speaker}</span>
          <div className="turn__text">{turn.text}</div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
