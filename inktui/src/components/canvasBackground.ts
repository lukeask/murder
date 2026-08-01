/** Shared preview-aware canvas fill for surfaces that write their own terminal cells. */

import { useContext } from 'react';
import { AppStoreContext } from '@murder/ui-core/hooks/useAppStore.js';
import {
  resolveBackgroundTransparency,
  useBackgroundTransparencyPreview,
} from '../terminal/canvasBackground.js';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';

/** The opaque app canvas below 100%; undefined deliberately exposes the terminal at 100%. */
export function useCanvasBackgroundColor(): string | undefined {
  // Pane is also deliberately used by store-free mode/component tests.  The surrounding App
  // already re-renders on persisted setting changes, while a stand-alone Pane simply exposes the
  // terminal canvas instead of requiring an unrelated AppStore provider.
  const appStore = useContext(AppStoreContext);
  const persistedTransparency = appStore?.getState().settings.backgroundTransparency ?? 100;
  useBackgroundTransparencyPreview();
  const theme = useTheme();
  return resolveBackgroundTransparency(persistedTransparency) < 100 ? theme.canvasBg : undefined;
}
