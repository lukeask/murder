import { Box } from 'ink';
import type { JSX, ReactNode } from 'react';
import { usePaneFocusLifecycle } from '../../../hooks/useInputStores.js';
import type { FocusId } from '../../../input/focusStore.js';
import type { PanePresentation } from '../../../layout/paneLayoutTypes.js';

export interface AllocatedPaneFrameProps {
  readonly id: FocusId;
  readonly presentation: PanePresentation;
  readonly children: ReactNode;
}

export function AllocatedPaneFrame({
  id,
  presentation,
  children,
}: AllocatedPaneFrameProps): JSX.Element {
  usePaneFocusLifecycle(id);

  return (
    <Box
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      {children}
    </Box>
  );
}
