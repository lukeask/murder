/**
 * BottomBar — contextual hints (the plan's "Bottom bar: contextual hints"). Shows the global chords
 * always, plus the *focused* panel's declared keys, sourced straight from its keymap so a declared
 * key is self-documenting (see keymap.ts). A pure function of the effective focus + the focused
 * panel's registered keymap; the formatting lives in {@link selectBottomBar} (rule 2).
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { useEffectiveFocus, useKeymapRegistry } from '../hooks/useInputStores.js';
import { CHAT_FOCUS } from '../input/focusStore.js';
import { selectBottomBar } from '../selectors/barSelectors.js';

export const BottomBar = memo(function BottomBar(): React.JSX.Element {
  const focused = useEffectiveFocus();
  // The focused panel's declared keymap (undefined when chat is focused — chat has no panel keymap).
  const focusedKeymap = useKeymapRegistry((s) =>
    focused === CHAT_FOCUS ? undefined : s.keymaps[focused]?.keymap,
  );
  const hints = useMemo(() => selectBottomBar(focused, focusedKeymap), [focused, focusedKeymap]);
  return (
    <Box flexDirection="row" columnGap={2} paddingX={1}>
      {hints.map((hint) => (
        <Text key={`${hint.key}:${hint.description}`} dimColor>
          <Text color="yellow">{hint.key}</Text> {hint.description}
        </Text>
      ))}
    </Box>
  );
});
