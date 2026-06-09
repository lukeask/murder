/**
 * paneBorder — the shared inline-title top-border row used by {@link ./Pane.tsx Pane} and
 * {@link ./ChatInput.tsx ChatInput}.
 *
 * Both want the `╭─ Title ──────────────╮` look: a hand-composed flex row drawn ON the top border
 * line, with the other three sides supplied by Ink's own `borderStyle="round"` + `borderTop={false}`
 * on the content box below. The recipe was first developed in `Pane` (see its header for the Yoga
 * quirks it relies on); rather than duplicate that JSX in ChatInput (spec nit: the chat input gets
 * the same border), the row is extracted here and used by both.
 *
 * Layout (one terminal line, `height={1}` so the `─` fill never wraps vertically):
 *
 *   ╭─  <title>   ───────────  ╮
 *   └fixed┘ └fixed┘ └fill────┘ └fixed┘
 *
 * The fixed segments are `flexShrink={0}` so only the fill absorbs the slack; the fill is a long
 * `─`-run in a `flexGrow` + `overflow="hidden"` box (`wrap="hard"`), so flexbox sizes it to the
 * leftover width and the box clips the overrun — no measured width, no setState, no flicker.
 *
 * Presentational only (rule 1): a pure function of its colors + title; no store/selector/bus access,
 * no `useInput` (rule 5). Colors arrive resolved (see {@link ./Pane.tsx paneColors}).
 */

import { Box, Text } from 'ink';

export interface PaneBorderTopProps {
  /** Display-ready title text shown inline on the border (e.g. `Plans`, or `›` for the chat input). */
  readonly title: string;
  /** Border + corner + `─`-fill color (green focused / gray blurred). */
  readonly borderColor: 'green' | 'gray';
  /** Title-segment color (green focused / white blurred — see {@link ./Pane.tsx paneColors}). */
  readonly titleColor: 'green' | 'white';
  /** True → render the title bold (matches the focused emphasis the old panels used). */
  readonly bold?: boolean;
  /** Optional trailing node placed right after the title text, inside the title segment. The CALLER
   * owns its color (pass a styled node) — see Pane's `titleExtra` handoff note. */
  readonly titleExtra?: React.ReactNode;
}

/**
 * The inline-title top-border row. `height={1}` keeps the `─` fill on a single line (otherwise
 * `wrap="hard"` would wrap the 256-char run vertically). The fill (`flexGrow` + `overflow="hidden"`)
 * absorbs the slack and clips cleanly; the fixed segments never shrink so the title is never elided.
 */
export function PaneBorderTop({
  title,
  borderColor,
  titleColor,
  bold = false,
  titleExtra,
}: PaneBorderTopProps): React.JSX.Element {
  return (
    <Box flexDirection="row" flexShrink={0} width="100%" height={1}>
      <Box flexShrink={0}>
        <Text color={borderColor}>{'╭─ '}</Text>
      </Box>
      <Box flexShrink={0}>
        <Text color={titleColor} bold={bold} wrap="truncate-end">
          {title}
        </Text>
        {titleExtra}
      </Box>
      <Box flexShrink={0}>
        <Text color={borderColor}> </Text>
      </Box>
      <Box flexGrow={1} flexShrink={1} minWidth={0} overflow="hidden">
        <Text color={borderColor} wrap="hard">
          {'─'.repeat(256)}
        </Text>
      </Box>
      <Box flexShrink={0}>
        <Text color={borderColor}>╮</Text>
      </Box>
    </Box>
  );
}
