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
