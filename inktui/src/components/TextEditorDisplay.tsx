import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { layoutEditor } from '../input/textEditor/layout.js';
import { type DisplayProjection, plainTextProjection } from '../input/textEditor/projection.js';
import type { TextEditorState } from '../input/textEditor/state.js';
import { useTheme } from '../theme/themeStore.js';

export interface TextEditorDisplayProps {
  readonly state: TextEditorState;
  readonly width: number;
  readonly projection?: DisplayProjection;
  readonly focused?: boolean;
  readonly placeholder?: string;
  readonly color?: string;
  /**
   * Retained for chat callers. Mid-text cursors always inverse the grapheme under the cursor so
   * layout width matches `layoutEditor`; end-of-buffer still uses a synthetic blank cell.
   */
  readonly cursorOnGlyph?: boolean;
}

/** Explicit row renderer for the shared text-editor layout. Ink never gets to choose a second wrap. */
export function TextEditorDisplay({
  state,
  width,
  projection = plainTextProjection,
  focused = false,
  placeholder,
  color,
  cursorOnGlyph: _cursorOnGlyph = false,
}: TextEditorDisplayProps): JSX.Element {
  const theme = useTheme();
  if (state.text.length === 0 && placeholder !== undefined) {
    if (!focused || placeholder.length === 0)
      return (
        <Box flexShrink={0}>
          <Text dimColor wrap="truncate-end">
            {placeholder}
          </Text>
        </Box>
      );
    return (
      <Box flexShrink={0}>
        <Text dimColor wrap="truncate-end">
          <Text inverse>{placeholder.slice(0, 1)}</Text>
          {placeholder.slice(1)}
        </Text>
      </Box>
    );
  }
  const layout = layoutEditor(state, width, projection);
  return (
    <Box flexDirection="column">
      {layout.rows.map((row, rowIndex) => {
        const active = focused && rowIndex === layout.cursorRow;
        const cursor = state.cursor;
        const body: React.ReactNode[] = [];
        let cursorRendered = false;
        for (const atom of row.atoms) {
          if (active && cursor === atom.sourceStart) {
            // Inverse the grapheme under the cursor so the row stays the same width as
            // `layoutEditor` predicts. A synthetic blank is only used at end-of-buffer.
            cursorRendered = true;
            body.push(
              <Text key={`${atom.sourceStart}:cursor`} inverse>
                {atom.text}
              </Text>,
            );
            continue;
          }
          body.push(<Text key={`${atom.sourceStart}:${atom.sourceEnd}`}>{atom.text}</Text>);
        }
        if (active && !cursorRendered)
          body.push(
            <Text key="end-cursor" inverse>
              {' '}
            </Text>,
          );
        return (
          <Box
            key={`${row.sourceStart}:${rowIndex}`}
            flexShrink={0}
            width={Math.max(1, Math.floor(width))}
          >
            <Text color={color ?? theme.text} wrap="truncate-end">
              {body.length === 0 ? ' ' : body}
            </Text>
          </Box>
        );
      })}
    </Box>
  );
}
