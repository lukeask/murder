/**
 * Sync `settings.workspaceCount` → workspace store via `applyWorkspaceCount`, and flash the
 * stage briefly on active-index changes (CSS only — no TUI slide frames).
 */

import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { applyWorkspaceCount } from '@murder/ui-core/input/workspaceSwitch.js';
import { useEffect, useRef, useState } from 'react';
import { useComposerStores, useWorkspaceStore } from './ComposerStoresProvider.js';
import { toWorkspaceStores } from './createComposerStores.js';

const STAGE_FLASH_MS = 180;

/** Bridge settings.workspace_count into the workspace store (grow/shrink + clamp hydrate). */
export function useWorkspaceCountSync(): void {
  const app = useAppStoreApi();
  const stores = useComposerStores();

  useEffect(() => {
    const wsStores = toWorkspaceStores(stores, app);
    const sync = (count: number): void => {
      applyWorkspaceCount(wsStores, count);
    };
    sync(app.getState().settings.workspaceCount);
    return app.subscribe((state, prev) => {
      if (state.settings.workspaceCount !== prev.settings.workspaceCount) {
        sync(state.settings.workspaceCount);
      }
    });
  }, [app, stores]);
}

/**
 * True for ~{@STAGE_FLASH_MS}ms after an active-index change so the stage can CSS-flash.
 * Skips the initial mount so boot doesn't flash.
 */
export function useWorkspaceSwitchFlash(): boolean {
  const activeIndex = useWorkspaceStore((s) => s.activeIndex);
  const [flashing, setFlashing] = useState(false);
  const prevIndex = useRef<number | null>(null);

  useEffect(() => {
    if (prevIndex.current === null) {
      prevIndex.current = activeIndex;
      return;
    }
    if (prevIndex.current === activeIndex) {
      return;
    }
    prevIndex.current = activeIndex;
    setFlashing(true);
    const id = window.setTimeout(() => setFlashing(false), STAGE_FLASH_MS);
    return () => window.clearTimeout(id);
  }, [activeIndex]);

  return flashing;
}
