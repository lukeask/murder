/**
 * ChatInput — the always-visible chat input and the focus *home* (the re-home destination). It is a
 * focusable like a panel — registers its rect, reads {@link useEffectiveFocus} for its highlight —
 * but it is **not** a `PanelId`: it can never be toggled off, so it uses the {@link CHAT_FOCUS}
 * literal as its focus id and lives outside the panel store.
 *
 * Scope here (C5): the always-visible, always-focusable input box with its highlight. The vim-style
 * text editing and message send (the `agent.message` action) are a later chunk (C10) — when chat is
 * focused, the root dispatcher already short-circuits raw keys to it (see dispatcher.ts), so the
 * editing widget slots in here without touching focus or the shell. This is a committed pattern only
 * for *focus participation*; the editor it will host is explicitly out of C5.
 *
 * **C10 seam — text-editor wiring deferred (manager: see note below):**
 * The `agent.message` RPC action (`conversationsActions.send`) is delivered and tested in C10.
 * However, wiring the *text input buffer* (char capture → display → enter-to-send) requires:
 *  1. A persistent C7M mode for chat (unlike transient modal modes, chat is always-focusable).
 *  2. `onUncaptured` char routing in the dispatcher's layer-2 chat short-circuit path.
 *  3. Replacing the placeholder `<Text>` with `<TextInput>` controlled by the mode's state.
 * This is architecturally non-trivial (it must not add a second `useInput` — rule 5; the C12
 * `onUncaptured` pattern is the correct approach but requires a new mode type). Deferred as a
 * follow-up chunk rather than implementing incompletely. The `send` action is available and fully
 * tested; the missing piece is only the UI char-capture-to-send pipeline.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { useEffectiveFocus, useFocusRef, useMeasureFocus } from '../hooks/useInputStores.js';
import { CHAT_FOCUS } from '../input/focusStore.js';

export const ChatInput = memo(function ChatInput(): React.JSX.Element {
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === CHAT_FOCUS;
  useMeasureFocus(CHAT_FOCUS, ref);
  return (
    <Box ref={ref} borderStyle="round" borderColor={focused ? 'green' : 'gray'} paddingX={1}>
      <Text dimColor={!focused}>{focused ? '› ' : '  '}message the collaborator…</Text>
    </Box>
  );
});
