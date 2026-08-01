/**
 * CaptureSheet — the mobile center-button sheet for the walk-and-ideas flows:
 * quick note, prompt an agent, or seed a feature spec into the composer.
 */

import { Icon, Sheet, type IconName } from '../ds/index.js';

export interface CaptureSheetProps {
  readonly onClose: () => void;
  /** Open the note-capture dialog. */
  readonly onNote: () => void;
  /** Jump to the chat stage with an empty composer. */
  readonly onPrompt: () => void;
  /** Jump to the chat stage with the feature-spec template seeded. */
  readonly onSpec: () => void;
}

const ACTIONS: readonly {
  readonly id: string;
  readonly icon: IconName;
  readonly label: string;
  readonly hint: string;
}[] = [
  { id: 'note', icon: 'edit', label: 'Quick note', hint: 'capture a thought before it flies off' },
  { id: 'prompt', icon: 'send', label: 'Prompt an agent', hint: 'unblock or redirect the murder' },
  { id: 'spec', icon: 'file-text', label: 'Feature spec', hint: 'seed the composer with a spec template' },
];

export function CaptureSheet({ onClose, onNote, onPrompt, onSpec }: CaptureSheetProps): React.JSX.Element {
  const handlers: Record<string, () => void> = { note: onNote, prompt: onPrompt, spec: onSpec };
  return (
    <Sheet title="Capture" onClose={onClose}>
      <div className="mw-actions">
        {ACTIONS.map((a) => (
          <button
            key={a.id}
            type="button"
            className="mw-action"
            onClick={() => {
              onClose();
              handlers[a.id]?.();
            }}
          >
            <Icon name={a.icon} size={20} />
            <span className="mw-action__text">
              <span className="mw-action__label">{a.label}</span>
              <span className="mw-action__hint">{a.hint}</span>
            </span>
          </button>
        ))}
      </div>
    </Sheet>
  );
}
