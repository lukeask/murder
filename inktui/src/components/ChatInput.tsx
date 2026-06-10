/**
 * ChatInput — the always-visible chat input and the focus *home* (the re-home destination). It is a
 * focusable like a panel — registers its rect, reads {@link useEffectiveFocus} for its highlight —
 * but it is **not** a `PanelId`: it can never be toggled off, so it uses the {@link CHAT_FOCUS}
 * literal as its focus id and lives outside the panel store.
 *
 * Scope here: the always-visible, always-focusable input box with its highlight, AND (C11, part F)
 * the live message buffer + send pipeline — the **persistent chat-input mode**.
 *
 * ## Persistent chat-input mode (C11, part F)
 *
 * Chat is the app's permanent focus home, so its text entry is NOT a transient {@link
 * ../input/modeStore.js modeStore} frame (that primitive is capture + focus-restore, which chat does
 * not want). Instead, when chat is the effective focus, the ONE root dispatcher (rule 5) routes the
 * (non-chord) event through its layer-2 chat short-circuit to a {@link ../input/dispatcher.js
 * ChatInputHandler} (built in `App.tsx`'s `Shell`). That handler buffers printable chars into the
 * {@link ../input/chatInputStore.js chatInput store}, deletes on Backspace, and on Enter sends the
 * buffer to the active agent via `conversations.send` (rule 3) and clears it. This component only
 * *renders* the buffer (read via `useChatInputStore`) with a {@link ./TextInput.js TextInput} — it
 * adds NO `useInput` (rule 5: still exactly one in the tree). Global ctrl-chords still fire while
 * typing because layer 1 preempts layer 2 in the dispatcher.
 *
 * ## Inline-title border (Phase 2 spec nit)
 * The input box wears the same `╭─ › ─────────╮` inline-title border as {@link ./Pane.tsx Pane}, via
 * the shared {@link ./paneBorder.js PaneBorderTop} row + a content box with `borderTop={false}`. The
 * title segment is the `›` prompt (green focused / white blurred, matching {@link ./Pane.tsx
 * paneColors}); the send target (`→ label`) + the text field live inside the bordered content row.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useChatInputStore,
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
} from '../hooks/useInputStores.js';
import { SPAN_CLOSE, SPAN_OPEN } from '../input/chatInputStore.js';
import { CHAT_FOCUS } from '../input/focusStore.js';
import { useActiveAgent } from '../selectors/conversationsSelectors.js';
import { PaneBorderTop } from './paneBorder.js';
import { TextInput } from './TextInput.js';

/** Matches one marked image span (`U+E000 <id> U+E001`) for render-time substitution. */
const SPAN_RE = new RegExp(`${SPAN_OPEN}[^${SPAN_OPEN}${SPAN_CLOSE}]*${SPAN_CLOSE}`, 'g');

/** Render-time display of the buffer: replace each invisible marked span with its derived visible
 * `[Image N]` label, numbered by position. The buffer holds *ids*, never the visible number, so this
 * positional counting renumbers for free when a span is deleted (F9). Plain text passes through. */
export function displayBuffer(text: string): string {
  let n = 0;
  return text.replace(SPAN_RE, () => `[Image ${++n}]`);
}

export const ChatInput = memo(function ChatInput(): React.JSX.Element {
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === CHAT_FOCUS;
  useMeasureFocus(CHAT_FOCUS, ref);
  // Rule 1: read exactly the chat buffer text. The dispatcher's chat handler owns mutation (rule 5).
  const text = useChatInputStore((s) => s.text);
  // The active send target (rule 2: derived in the selector), so the box shows *who* a typed message
  // goes to — the same resolution the Enter handler uses, surfaced live.
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const target = useActiveAgent(conversations, roster, favorites);
  const targetLabel = target === null ? 'no target' : target.label;
  // F9: marked image spans (invisible PUA-wrapped ids) render as derived `[Image N]` labels.
  const display = displayBuffer(text);
  const borderColor = focused ? 'green' : 'gray';
  const titleColor = focused ? 'green' : 'white';
  return (
    // Inline-title border (Pane recipe): the `›` prompt sits on the top border line; the send target
    // + text field live inside the content box, which supplies the other three sides + padding.
    <Box ref={ref} flexDirection="column">
      <PaneBorderTop title="›" borderColor={borderColor} titleColor={titleColor} bold={focused} />
      <Box
        flexDirection="row"
        borderStyle="round"
        borderTop={false}
        borderColor={borderColor}
        paddingX={1}
      >
        <Text bold color={focused ? 'green' : 'gray'} wrap="truncate">
          {`→ ${targetLabel} `}
        </Text>
        <TextInput value={display} placeholder="type a message" focused={focused} />
      </Box>
    </Box>
  );
});
