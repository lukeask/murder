/**
 * PanelToggleStrip — TUI TopBar panel labels in the web NavBar beam.
 * Digit-subscript labels from {@link selectTopBar}; click toggles rail visibility
 * via {@link togglePanelVisibility} (same as digit chords).
 */

import { selectTopBar } from '@murder/ui-core/selectors/barSelectors.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import type { PanelState } from '@murder/ui-core/input/panelStore.js';
import { useMemo } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import { useComposerStores } from '../composer/ComposerStoresProvider.js';
import { togglePanelVisibility } from '../panelVisibility.js';
import { cx } from './ds/cx.js';

function visibleSetEqual(a: ReadonlySet<PanelId>, b: ReadonlySet<PanelId>): boolean {
  if (a === b) return true;
  if (a.size !== b.size) return false;
  for (const id of a) {
    if (!b.has(id)) return false;
  }
  return true;
}

/** Compact ledger row of panel labels; active = visible; click toggles. */
export function PanelToggleStrip(): React.JSX.Element {
  const { panels } = useComposerStores();
  const visible = useStoreWithEqualityFn(
    panels,
    (s: PanelState) => s.visible,
    visibleSetEqual,
  );
  const labels = useMemo(() => selectTopBar(visible), [visible]);

  return (
    <div className="panel-toggle" role="toolbar" aria-label="Toggle panels">
      {labels.map((label) => (
        <button
          key={label.id}
          type="button"
          className={cx(
            'panel-toggle__label',
            label.active && 'panel-toggle__label--active',
            label.dividerBefore === true && 'panel-toggle__label--rail-break',
          )}
          aria-pressed={label.active}
          title={label.active ? `Hide ${label.id}` : `Show ${label.id}`}
          onClick={() => togglePanelVisibility(panels, label.id)}
        >
          {label.dividerBefore === true ? (
            <span className="panel-toggle__sep" aria-hidden="true">
              ·
            </span>
          ) : null}
          {label.text}
        </button>
      ))}
    </div>
  );
}
