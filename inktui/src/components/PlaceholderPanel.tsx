/**
 * PlaceholderPanel — a clearly-labelled stand-in for a panel a *later* chunk fills in (left region
 * plans/notes/reports/tickets → C6/C7; usage → C9). It is NOT the reference component — {@link
 * ./RosterPanel.js} is. Its only job is to make the shell's composition, region layout, focus ring,
 * and toggling demonstrably real *now*, before every panel exists: it participates in focus exactly
 * like a real panel (registers its rect, highlights via {@link useEffectiveFocus}) so directional
 * nav and the re-home invariant work across the whole layout, but it declares no keymap and renders
 * a "filled by Cn" notice instead of data.
 *
 * When the owning chunk lands, it replaces the placeholder for its id with a real panel copied from
 * {@link ./RosterPanel.js} — the shell wiring in {@link ./App.js} does not change, only which
 * component the region renders for that id.
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { useEffectiveFocus, useFocusRef, useMeasureFocus } from '../hooks/useInputStores.js';
import type { PanelId } from '../input/panels.js';

export const PlaceholderPanel = memo(function PlaceholderPanel({
  id,
  title,
  filledBy,
}: {
  readonly id: PanelId;
  readonly title: string;
  /** The chunk that replaces this placeholder, e.g. `'C6'` — surfaced in the UI so the seam is
   * visible to anyone running the shell, not buried in a comment. */
  readonly filledBy: string;
}): React.JSX.Element {
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === id;
  useMeasureFocus(id, ref);
  return (
    <Box
      ref={ref}
      flexDirection="column"
      borderStyle="round"
      borderColor={focused ? 'green' : 'gray'}
      paddingX={1}
      flexGrow={1}
    >
      <Text bold color={focused ? 'green' : 'white'}>
        {title}
      </Text>
      <Text dimColor>{`${filledBy} fills this`}</Text>
    </Box>
  );
});
