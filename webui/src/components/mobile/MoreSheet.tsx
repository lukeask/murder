/**
 * MoreSheet — mobile "More" destination: the secondary panels (workflows, plans, reports,
 * history, usage, tree, settings) as a sheet list instead of ten squished tabs.
 */

import { Icon, Sheet, type IconName } from '../ds/index.js';

export interface MoreSheetItem {
  readonly id: string;
  readonly label: string;
  readonly icon: IconName;
}

export interface MoreSheetProps {
  readonly items: readonly MoreSheetItem[];
  /** The currently mounted pane, highlighted when it is a secondary one. */
  readonly activeId: string | null;
  readonly onSelect: (id: string) => void;
  readonly onClose: () => void;
}

export function MoreSheet({ items, activeId, onSelect, onClose }: MoreSheetProps): React.JSX.Element {
  return (
    <Sheet title="More" onClose={onClose}>
      <div className="mw-actions">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className="mw-action"
            data-on={item.id === activeId ? 'true' : undefined}
            onClick={() => {
              onClose();
              onSelect(item.id);
            }}
          >
            <Icon name={item.icon} size={20} />
            <span className="mw-action__text">
              <span className="mw-action__label">{item.label}</span>
            </span>
          </button>
        ))}
      </div>
    </Sheet>
  );
}
