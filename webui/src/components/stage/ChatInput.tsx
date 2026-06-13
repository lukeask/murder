/**
 * ChatInput — the message editor for the active chat target. Maps to the `conversations` slice:
 * the active agent comes from {@link selectActiveAgentId}; Enter sends via `conversations.send`.
 *
 * Live multiple-choice takeover: when the active transcript ends in an unanswered `choice_prompt`
 * ({@link selectLiveChoicePrompt} ≠ null), the input forwards keys to the agent's pane via
 * `conversations.sendKey` instead of buffering text — the same contract the Ink ChatInput uses.
 * Image-paste (the Ink ctrl+v draft flow) is intentionally deferred for the web port; see report.
 */

import {
  selectActiveAgentId,
  selectLiveChoicePrompt,
} from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';

export function ChatInput(): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const send = useAppStore((s) => s.actions.conversations.send);
  const sendKey = useAppStore((s) => s.actions.conversations.sendKey);
  const [text, setText] = useState('');

  const agentId = selectActiveAgentId(conversations, roster, favorites);
  const livePrompt = agentId === null ? null : selectLiveChoicePrompt(conversations, agentId);

  const submit = (): void => {
    if (agentId === null || text.trim() === '') {
      return;
    }
    void send(agentId, text);
    setText('');
  };

  // When a live choice dialog is up, the input answers IT: numbered options forward a digit; the
  // raw text forwards as literal and Enter confirms. Keys go to the pane via sendKey (rule 3 path).
  const onKeyDownChoice = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (agentId === null) return;
    const map: Record<string, string> = {
      ArrowUp: 'Up',
      ArrowDown: 'Down',
      ArrowLeft: 'Left',
      ArrowRight: 'Right',
      Enter: 'Enter',
      Escape: 'Escape',
      ' ': 'Space',
      Backspace: 'BSpace',
    };
    const named = map[e.key];
    if (named !== undefined) {
      e.preventDefault();
      void sendKey(agentId, named, false);
      return;
    }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      void sendKey(agentId, e.key, true);
    }
  };

  if (livePrompt !== null && agentId !== null) {
    return (
      <div className="chat-input chat-input--choice">
        <div className="chat-input__prompt">
          <span className="chat-input__prompt-q">{livePrompt.question}</span>
          <ol className="chat-input__options">
            {livePrompt.options.map((opt) => (
              <li
                key={opt.number}
                data-selected={livePrompt.selected === opt.number ? 'true' : undefined}
                onClick={() => void sendKey(agentId, String(opt.number), true)}
              >
                {opt.label}
              </li>
            ))}
          </ol>
        </div>
        <input
          className="chat-input__field"
          placeholder="answer (keys forward to the agent)…"
          onKeyDown={onKeyDownChoice}
          autoFocus
        />
      </div>
    );
  }

  return (
    <div className="chat-input">
      <input
        className="chat-input__field"
        placeholder={agentId === null ? 'select a crow to chat…' : `message ${agentId}…`}
        value={text}
        disabled={agentId === null}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button type="button" className="chat-input__send" disabled={agentId === null} onClick={submit}>
        send
      </button>
    </div>
  );
}
