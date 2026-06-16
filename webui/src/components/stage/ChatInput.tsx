/**
 * ChatInput — the message editor for the active chat target, reskinned onto the DS composer
 * (crow-chat / web-cockpit): a meta row ("to ⭐ crowname" + a KeyHint) above a DS Input (size lg) with
 * a leading attach IconButton and a trailing send IconButton that fills `--accent` when the draft is
 * non-empty. Data wiring is UNCHANGED (rule 2): active agent from {@link selectActiveAgentId};
 * Enter sends via `conversations.send`; the live-choice takeover forwards keys via `conversations.sendKey`.
 *
 * BOTH render paths preserved:
 *  - live-choice-prompt path: numbered options list + key-forwarding (`onKeyDownChoice`) unchanged.
 *  - normal text path: Enter (no shift) to send; the trailing send button mirrors that.
 * Image-paste (the Ink ctrl+v draft flow) is intentionally deferred for the web port; see report.
 */

import {
  selectActiveAgentId,
  selectLiveChoicePrompt,
} from '@core/selectors/conversationsSelectors.js';
import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Input, IconButton, KeyHint, Icon } from '../ds/index.js';

export function ChatInput(): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const send = useAppStore((s) => s.actions.conversations.send);
  const sendKey = useAppStore((s) => s.actions.conversations.sendKey);
  const [text, setText] = useState('');

  const agentId = selectActiveAgentId(conversations, roster, favorites);
  const livePrompt = agentId === null ? null : selectLiveChoicePrompt(conversations, agentId);
  const canSend = agentId !== null && text.trim() !== '';

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

  const metaRow = (
    <div className="mds-composer__meta">
      <span className="mds-composer__to">
        <span className="star">★</span>
        <span className="mds-composer__to-name">{agentId ?? 'no crow'}</span>
      </span>
      <KeyHint chord="Enter" desc="send" tone="muted" />
    </div>
  );

  if (livePrompt !== null && agentId !== null) {
    return (
      <div className="mds-composer">
        {metaRow}
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
        <Input
          size="lg"
          placeholder="answer (keys forward to the agent)…"
          onKeyDown={onKeyDownChoice}
          autoFocus
          leading={
            <span className="mds-composer__attach">
              <Icon name="paperclip" />
            </span>
          }
        />
      </div>
    );
  }

  return (
    <div className="mds-composer">
      {metaRow}
      <Input
        size="lg"
        value={text}
        disabled={agentId === null}
        placeholder={agentId === null ? 'select a crow to chat…' : `message ${agentId}…`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        leading={
          <span className="mds-composer__attach">
            <Icon name="paperclip" />
          </span>
        }
        trailing={
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
        }
      />
    </div>
  );
}
