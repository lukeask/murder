/**
 * TopBar — highlights the currently-*toggled* panels with subscript number labels (`plans₁ … crows₀`,
 * the plan's "Top bar: highlight currently-toggled panels"). A pure function of the panel store's
 * visible set: it reads the set, runs the {@link selectTopBar} view-model (rule 2 — the label
 * formatting lives in the selector), and paints each label highlighted iff its panel is on.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { usePanelStore } from '../hooks/useInputStores.js';
import { selectTopBar } from '../selectors/barSelectors.js';

export const TopBar = memo(function TopBar(): React.JSX.Element {
  const visible = usePanelStore((s) => s.visible);
  // The selector turns the visible set into render-ready labels; memoised on the set identity (the
  // panel store ref-swaps the set only on change, so this re-formats only on a real toggle).
  const labels = useMemo(() => selectTopBar(visible), [visible]);
  return (
    <Box flexDirection="row" columnGap={1} paddingX={1}>
      {labels.map((label) => (
        // Toggled panels are bold/coloured; off panels are dim — so the bar reads view state at a
        // glance (the plan's whole point: the top bar shows what's on, not just the active view).
        <Text key={label.id} bold={label.active} color={label.active ? 'green' : 'gray'}>
          {label.text}
        </Text>
      ))}
    </Box>
  );
});
