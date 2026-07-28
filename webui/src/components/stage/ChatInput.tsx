/** ChatInput — composer for the active chat target; live-choice forwards keys via sendKey. */

import {
  selectActiveAgentId,
  selectLiveChoicePrompt,
} from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Input, IconButton, KeyHint, Icon } from '../ds/index.js';
import { CHAT_INPUT_ID } from '../../useDesktopKeybinds.js';

export function ChatInput(): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const send = useAppStore((s) => s.actions.conversations.send);
  const sendKey = useAppStore((s) => s.actions.conversations.sendKey);
  const [text, setText] = useState('');

  const agentId = selectActiveAgentId(conversations, roster, favorites);
  const livePrompt = agentId === null ? null : selectLiveChoicePrompt(conversations, agentId);
  const isChoice = livePrompt !== null && agentId !== null;
  const canSend = agentId !== null && text.trim() !== '';

  const submit = (): void => {
    if (agentId === null || text.trim() === '') return;
    void send(agentId, text);
    setText('');
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (isChoice) {
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
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="mds-composer">
      <div className="mds-composer__meta">
        <span className="mds-composer__to">
          <span className="star">★</span>
          <span className="mds-composer__to-name">{agentId ?? 'no crow'}</span>
        </span>
        <KeyHint chord="Enter" desc="send" tone="muted" />
      </div>
      {isChoice ? (
        <div className="mds-composer__prompt">
          <span className="mds-composer__prompt-q">{livePrompt.question}</span>
          <ol className="mds-composer__options">
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
      ) : null}
      <Input
        size="lg"
        placeholder={
          isChoice
            ? 'answer (keys forward to the agent)…'
            : agentId === null
              ? 'select a crow to chat…'
              : `message ${agentId}…`
        }
        onKeyDown={onKeyDown}
        {...(isChoice
          ? { autoFocus: true as const }
          : {
              id: CHAT_INPUT_ID,
              value: text,
              disabled: agentId === null,
              onChange: (e: React.ChangeEvent<HTMLInputElement>) => setText(e.target.value),
              trailing: (
                <IconButton
                  label="send"
                  size="md"
                  disabled={!canSend}
                  onClick={submit}
                  style={
                    canSend
                      ? { background: 'var(--accent)', color: 'var(--text-on-accent)' }
                      : undefined
                  }
                >
                  <Icon name="send" />
                </IconButton>
              ),
            })}
      />
    </div>
  );
}
