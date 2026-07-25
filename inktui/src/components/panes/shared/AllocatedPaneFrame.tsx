import { useOnClick } from '@ink-tools/ink-mouse';
import { Box, type DOMElement } from 'ink';
import { type JSX, type ReactNode, useRef } from 'react';
import { useInputStores, usePaneFocusLifecycle } from '../../../hooks/useInputStores.js';
import type { FocusId } from '../../../input/focusStore.js';
import { wasMouseClickClaimed } from '../../../input/mouseClick.js';
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
  const { focus } = useInputStores();
  const ref = useRef<DOMElement>(null);

  useOnClick(ref, (event) => {
    if (event.button !== 'left') {
      return;
    }
    // Nested list-row handlers claim the event; defer so they run first regardless of registration
    // order (ink-mouse fires every matching handler with no stopPropagation).
    queueMicrotask(() => {
      if (wasMouseClickClaimed(event)) {
        return;
      }
      focus.getState().focus(id);
    });
  });

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
