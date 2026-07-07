import {
  BAR_WIDGET_DEFINITIONS,
  type BarPlacement,
  type BarWidgetId,
} from '../../../selectors/barWidgetRegistry.js';
import { HARNESSES } from './harnesses.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

const PLACEMENTS: readonly BarPlacement[] = ['top', 'bottom'];

function widgetRows(def: (typeof BAR_WIDGET_DEFINITIONS)[number]): SettingsRow[] {
  const id = def.id;
  const rows: SettingsRow[] = [headerRow({ id: `bars.${id}`, label: def.label, rows: () => [] })];
  rows.push(
    {
      id: `bars.${id}:enabled:off`,
      kind: 'barWidget',
      widgetId: id,
      field: 'enabled',
      value: false,
    },
    { id: `bars.${id}:enabled:on`, kind: 'barWidget', widgetId: id, field: 'enabled', value: true },
  );
  const placements =
    def.allowedPlacements.length === 1
      ? def.allowedPlacements
      : PLACEMENTS.filter((p) => def.allowedPlacements.includes(p));
  if (placements.length > 1) {
    rows.push(
      ...placements.map(
        (placement): SettingsRow => ({
          id: `bars.${id}:placement:${placement}`,
          kind: 'barWidget',
          widgetId: id,
          field: 'placement',
          value: placement,
        }),
      ),
    );
  }
  if (id === 'usage') {
    rows.push(
      ...HARNESSES.map(
        (value): SettingsRow => ({
          id: `bars.${id}:harness:${value}`,
          kind: 'barWidgetHarness',
          widgetId: id,
          value,
        }),
      ),
    );
  }
  if (id === 'hints') {
    rows.push(
      {
        id: `bars.${id}:adaptive:on`,
        kind: 'barWidget',
        widgetId: id,
        field: 'adaptive',
        value: true,
      },
      {
        id: `bars.${id}:adaptive:off`,
        kind: 'barWidget',
        widgetId: id,
        field: 'adaptive',
        value: false,
      },
    );
  }
  return rows;
}

export const BAR_ITEMS: readonly SettingsItem[] = BAR_WIDGET_DEFINITIONS.map((def) => ({
  id: `bars.${def.id}`,
  label: def.label,
  rows: () => widgetRows(def),
}));

export type { BarPlacement, BarWidgetId };
