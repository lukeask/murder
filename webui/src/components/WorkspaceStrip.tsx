/**
 * WorkspaceStrip — digit strip for workspaces 1..N, integrated into the nav beam / header.
 * Hidden when count ≤ 1 (feature inert). Click jumps; active cell uses focus accent fill.
 */

import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useComposerStores, useWorkspaceStore } from '../composer/ComposerStoresProvider.js';
import { toWorkspaceStores } from '../composer/createComposerStores.js';
import { workspaceJump } from '../composer/workspaceActions.js';
import { cx } from './ds/cx.js';

export interface WorkspaceStripProps {
  readonly className?: string;
}

/** Digit strip: one cell per workspace; active filled with focus accent. */
export function WorkspaceStrip({ className }: WorkspaceStripProps): React.JSX.Element | null {
  const count = useWorkspaceStore((s) => s.count);
  const activeIndex = useWorkspaceStore((s) => s.activeIndex);
  const stores = useComposerStores();
  const app = useAppStoreApi();

  if (count <= 1) {
    return null;
  }

  const onJump = (index: number): void => {
    workspaceJump(toWorkspaceStores(stores, app), index);
  };

  return (
    <div
      className={cx('workspace-strip', className)}
      role="tablist"
      aria-label="workspaces"
    >
      {Array.from({ length: count }, (_, i) => (
        <button
          key={i}
          type="button"
          role="tab"
          aria-selected={i === activeIndex}
          aria-label={`workspace ${i + 1}`}
          className={cx(
            'workspace-strip__cell',
            i === activeIndex && 'workspace-strip__cell--active',
          )}
          onClick={() => onJump(i)}
        >
          {i + 1}
        </button>
      ))}
    </div>
  );
}
