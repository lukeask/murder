/**
 * TopBar — a small **branding** mark (`murder · <project>`), a gap, then the panel labels that
 * highlight the currently-*toggled* panels with subscript numbers (`plans₁ … crows₀`, the plan's
 * "Top bar: highlight currently-toggled panels"). The labels are a pure function of the panel
 * store's visible set: it reads the set, runs the {@link selectTopBar} view-model (rule 2 — the
 * label formatting lives in the selector), and paints each label highlighted iff its panel is on.
 *
 * `project` is the current project/repo name, threaded from the entrypoint (the launcher hands it
 * over via `MURDER_PROJECT`; see index.tsx). When unknown (smoke/tests) only the `murder` mark shows.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { usePanelStore } from '../hooks/useInputStores.js';
import { selectTopBar } from '../selectors/barSelectors.js';

export const TopBar = memo(function TopBar({
  project,
}: {
  readonly project?: string | undefined;
}): React.JSX.Element {
  const visible = usePanelStore((s) => s.visible);
  // The selector turns the visible set into render-ready labels; memoised on the set identity (the
  // panel store ref-swaps the set only on change, so this re-formats only on a real toggle).
  const labels = useMemo(() => selectTopBar(visible), [visible]);
  return (
    <Box flexDirection="row" paddingX={1}>
      {/* Branding: a bold `murder` mark + the dim project name, then a gap before the panel labels. */}
      <Box flexDirection="row" columnGap={1} marginRight={3}>
        <Text bold color="redBright">
          murder
        </Text>
        {project !== undefined && project.length > 0 && <Text color="gray">{project}</Text>}
      </Box>
      <Box flexDirection="row" columnGap={1}>
        {labels.map((label) => (
          // Toggled panels are bold/coloured; off panels are dim — so the bar reads view state at a
          // glance (the plan's whole point: the top bar shows what's on, not just the active view).
          <Text key={label.id} bold={label.active} color={label.active ? 'green' : 'gray'}>
            {label.text}
          </Text>
        ))}
      </Box>
    </Box>
  );
});
