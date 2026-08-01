/**
 * One-line “+ create new” row painted above a Ledger so create stays 1 lh while data rows
 * keep their own linesPerEntry.
 */

import { type InkMouseEvent, useOnClick } from '@ink-tools/ink-mouse';
import { Box, type DOMElement, Text } from 'ink';
import { memo, useRef } from 'react';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';

export interface CreateRowProps {
  readonly label: string;
  readonly selected: boolean;
  readonly width?: number;
  readonly onClick?: (event: InkMouseEvent) => void;
}

export const CreateRow = memo(function CreateRow({
  label,
  selected,
  width,
  onClick,
}: CreateRowProps): React.JSX.Element {
  const theme = useTheme();
  const bg = selected ? theme.rowSelectedBg : undefined;
  if (onClick === undefined) {
    return (
      <Box flexShrink={0} {...(width !== undefined ? { width } : { width: '100%' })} backgroundColor={bg}>
        <Text wrap="truncate" color={theme.text}>
          {label}
        </Text>
      </Box>
    );
  }
  return (
    <ClickableCreateRow
      label={label}
      selected={selected}
      {...(width !== undefined ? { width } : {})}
      onClick={onClick}
    />
  );
});

function ClickableCreateRow({
  label,
  selected,
  width,
  onClick,
}: {
  readonly label: string;
  readonly selected: boolean;
  readonly width?: number;
  readonly onClick: (event: InkMouseEvent) => void;
}): React.JSX.Element {
  const theme = useTheme();
  const ref = useRef<DOMElement>(null);
  const bg = selected ? theme.rowSelectedBg : undefined;
  useOnClick(ref, (event) => {
    if (event.button !== 'left') {
      return;
    }
    onClick(event);
  });
  return (
    <Box
      ref={ref}
      flexShrink={0}
      {...(width !== undefined ? { width } : { width: '100%' })}
      backgroundColor={bg}
    >
      <Text wrap="truncate" color={theme.text}>
        {label}
      </Text>
    </Box>
  );
}
