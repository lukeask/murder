/**
 * TopBar — a small **branding** mark (`murder · <project>`), a gap, then the panel labels that
 * highlight the currently-*toggled* panels with subscript numbers (`plans₁ … crows₀`, the plan's
 * "Top bar: highlight currently-toggled panels"). The labels are a pure function of the panel
 * store's visible set: it reads the set, runs the {@link selectTopBar} view-model (rule 2 — the
 * label formatting lives in the selector), and paints each label highlighted iff its panel is on.
 *
 * Enabled top-bar widgets (Phase 3.1) render in the remaining right-side space before the connection
 * badge; they truncate/drop rather than wrap so the bar stays exactly one line.
 *
 * `project` is the current project/repo name, threaded from the entrypoint (the launcher hands it
 * over via `MURDER_PROJECT`; see index.tsx). When unknown (smoke/tests) only the `murder` mark shows.
 */

import { Box, Text } from 'ink';
import { memo, useMemo } from 'react';
import { useAppStore } from '../hooks/useAppStore.js';
import { usePanelStore } from '../hooks/useInputStores.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import {
  connectionBadgeWidth,
  estimateTopBarLeftWidth,
  layoutTopBarWidgets,
  selectTopBar,
  selectTopBarWidgetSegments,
  TOP_BAR_PADDING,
  TOP_BAR_RIGHT_CLUSTER_GAP,
  type TopBarWidgetSegment,
} from '../selectors/barSelectors.js';
import { type ConnectionStatus, useConnectionStatus } from '../store/connection/connectionStore.js';
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';
import { TextRuns } from './TextRuns.js';

function TopBarWidgetCluster({
  segments,
}: {
  readonly segments: readonly TopBarWidgetSegment[];
}): React.JSX.Element | null {
  if (segments.length === 0) {
    return null;
  }
  return (
    <Box flexDirection="row" columnGap={1}>
      {segments.map((segment) => (
        <TextRuns key={segment.widgetId} runs={segment.runs} />
      ))}
    </Box>
  );
}

export const TopBar = memo(function TopBar({
  project,
}: {
  readonly project?: string | undefined;
}): React.JSX.Element {
  const theme = useTheme();
  const status = useConnectionStatus();
  const visible = usePanelStore((s) => s.visible);
  const barWidgets = useAppStore((s) => s.settings.barWidgets);
  const usage = useAppStore((s) => s.usage);
  const { columns } = useTerminalSize();
  const labels = useMemo(() => selectTopBar(visible), [visible]);
  const rawSegments = useMemo(
    () => selectTopBarWidgetSegments(barWidgets, { usage, keyUsage: {}, now: 0 }),
    [barWidgets, usage],
  );
  const widgetAvail = useMemo(() => {
    const left = estimateTopBarLeftWidth(project, labels);
    const badge = connectionBadgeWidth(status);
    const clusterGap = badge > 0 || rawSegments.length > 0 ? TOP_BAR_RIGHT_CLUSTER_GAP : 0;
    return Math.max(0, columns - TOP_BAR_PADDING - left - badge - clusterGap);
  }, [columns, project, labels, status, rawSegments.length]);
  const widgetSegments = useMemo(
    () => layoutTopBarWidgets(rawSegments, widgetAvail),
    [rawSegments, widgetAvail],
  );
  return (
    <Box flexDirection="row" paddingX={1} justifyContent="space-between">
      <Box flexDirection="row">
        <Box flexDirection="row" columnGap={1} marginRight={3}>
          <Text bold color={theme.brand}>
            murder
          </Text>
          {project !== undefined && project.length > 0 && (
            <Text color={theme.muted}>{`· ${project}`}</Text>
          )}
        </Box>
        <Box flexDirection="row" columnGap={1}>
          {labels.map((label) => (
            <Box key={label.id} flexDirection="row" columnGap={1}>
              {label.dividerBefore === true && <Text color={theme.muted}>·</Text>}
              <Text bold={label.active} color={label.active ? theme.active : theme.inactive}>
                {label.text}
              </Text>
            </Box>
          ))}
        </Box>
      </Box>
      <Box flexDirection="row" columnGap={1}>
        <TopBarWidgetCluster segments={widgetSegments} />
        <ConnectionBadge status={status} theme={theme} />
      </Box>
    </Box>
  );
});

/**
 * The connection-state badge, pinned right. Silent (renders nothing) for the two steady states a
 * user need not be told about — `'connected'` (the happy path) and `'unknown'` (no wiring has
 * reported, i.e. smoke/tests/fake-bus). The three transitional/broken states each earn a badge:
 *  - `'connecting'` — a dim `connecting…` while the first handshake is in flight;
 *  - `'reconnecting'` — a {@link Theme.warning warning}-coloured `[reconnecting]` while backoff retries;
 *  - `'version-mismatch'` — a {@link Theme.error error}-coloured `[version mismatch — restart murder]`,
 *    the one permanent, user-actionable failure.
 */
function ConnectionBadge({
  status,
  theme,
}: {
  readonly status: ConnectionStatus;
  readonly theme: Theme;
}): React.JSX.Element | null {
  switch (status) {
    case 'connecting':
      return <Text dimColor>connecting…</Text>;
    case 'reconnecting':
      return <Text color={theme.warning}>[reconnecting]</Text>;
    case 'version-mismatch':
      return <Text color={theme.error}>[version mismatch — restart murder]</Text>;
    default:
      return null;
  }
}
