import { Box } from 'ink';
import type { JSX, ReactNode } from 'react';
import { useFocusRef, useMeasureFocus } from '../../../hooks/useInputStores.js';
import type { FocusId } from '../../../input/focusStore.js';
import type { PanePresentation } from '../../../layout/paneLayoutTypes.js';

export interface MeasuredPaneFrameProps {
  readonly id: FocusId;
  readonly presentation: PanePresentation;
  readonly children: ReactNode;
}

export function MeasuredPaneFrame({
  id,
  presentation,
  children,
}: MeasuredPaneFrameProps): JSX.Element {
  const ref = useFocusRef();
  useMeasureFocus(id, ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      {children}
    </Box>
  );
}
