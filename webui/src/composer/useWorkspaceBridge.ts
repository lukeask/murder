/**
 * Flash the stage briefly on active-index changes (CSS only — no TUI slide frames), and expose
 * a Settings hook for per-repo workspace count (Phase 8 — localStorage, not settings.workspace_count).
 */

import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useEffect, useRef, useState } from 'react';
import { useComposerStores, useWorkspaceStore } from './ComposerStoresProvider.js';
import { toWorkspaceStores } from './createComposerStores.js';
import { applyWorkspaceCount } from './workspaceActions.js';

const STAGE_FLASH_MS = 180;

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

/** Apply a Settings UI workspace-count change to the live bag + localStorage. */
export function useApplyWorkspaceCountFromSettings(): (count: number) => void {
  const app = useAppStoreApi();
  const stores = useComposerStores();
  return (count: number): void => {
    applyWorkspaceCount(toWorkspaceStores(stores, app), count);
  };
}
