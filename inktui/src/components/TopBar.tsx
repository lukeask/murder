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
import { type ConnectionStatus, useConnectionStatus } from '../store/connection/connectionStore.js';
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';

export const TopBar = memo(function TopBar({
  project,
}: {
  readonly project?: string | undefined;
}): React.JSX.Element {
  const theme = useTheme();
  const status = useConnectionStatus();
  const visible = usePanelStore((s) => s.visible);
  // The selector turns the visible set into render-ready labels; memoised on the set identity (the
  // panel store ref-swaps the set only on change, so this re-formats only on a real toggle).
  const labels = useMemo(() => selectTopBar(visible), [visible]);
  return (
    // `justifyContent="space-between"` pins the connection badge to the right edge while the existing
    // branding + panel-label group stays left, so neither shifts the other (the badge is silent —
    // and absent — for the steady `connected`/`unknown` states, see `ConnectionBadge`).
    <Box flexDirection="row" paddingX={1} justifyContent="space-between">
      <Box flexDirection="row">
        {/* Branding: a bold `murder` mark + the dim project name, then a gap before the panel labels. */}
        <Box flexDirection="row" columnGap={1} marginRight={3}>
          <Text bold color={theme.brand}>
            murder
          </Text>
          {project !== undefined && project.length > 0 && (
            // A middot separator keeps the coral brand and the project name from running together
            // (`murder · testingmurderharness`), since adjacent Text nodes otherwise abut visually.
            <Text color={theme.muted}>{`· ${project}`}</Text>
          )}
        </Box>
        <Box flexDirection="row" columnGap={1}>
          {labels.map((label) => (
            // Toggled panels are bold/coloured; off panels are dim — so the bar reads view state at a
            // glance (the plan's whole point: the top bar shows what's on, not just the active view).
            <Text
              key={label.id}
              bold={label.active}
              color={label.active ? theme.active : theme.inactive}
            >
              {label.text}
            </Text>
          ))}
        </Box>
      </Box>
      <ConnectionBadge status={status} theme={theme} />
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
      // 'connected' and 'unknown' show no badge.
      return null;
  }
}
