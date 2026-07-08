/**
 * Bar widget registry — the extension point for 1-line-height top/bottom bar segments (Phase 3.1).
 * Each built-in widget declares defaults (placement, enabled) and metadata for the Settings "Bars"
 * category; {@link resolveBarWidgetConfig} merges persisted `bar_widgets` entries onto those defaults.
 *
 * To add a widget: register a {@link BarWidgetDefinition}, add a selector branch in
 * `barSelectors.ts`, and append settings rows in `components/settings/items/bars.ts`.
 */

/** Known built-in widget ids. Extend this union when registering a new widget. */
export type BarWidgetId = 'hints' | 'usage' | 'workspace';

export type BarPlacement = 'top' | 'bottom';

/** One widget's persisted preferences (mirrors Python `BarWidgetUserConfig`). */
export interface BarWidgetUserConfig {
  readonly enabled: boolean;
  readonly placement: BarPlacement;
  /** When true (default), the hints widget picks one adaptive line from usage-ranked hints. */
  readonly adaptive?: boolean;
  /** Harness ids feeding the min-timer (usage widget only). Empty/omitted = all harnesses. */
  readonly harnesses?: readonly string[];
}

/** The wire / YAML shape: partial entries per widget id (snake_case keys on the bus). */
export type BarWidgetsWire = Readonly<Partial<Record<BarWidgetId, BarWidgetUserConfig>>>;

/** Slice-side map — same keys, camelCase container name only. */
export type BarWidgetsConfig = BarWidgetsWire;

export interface BarWidgetDefinition {
  readonly id: BarWidgetId;
  /** Settings UI label. */
  readonly label: string;
  readonly defaultEnabled: boolean;
  readonly defaultPlacement: BarPlacement;
  /** When set, the placement radio only offers these values (hints are bottom-only). */
  readonly allowedPlacements: readonly BarPlacement[];
}

/** Built-in widgets in registration order (bottom-bar packing uses this order). */
export const BAR_WIDGET_DEFINITIONS: readonly BarWidgetDefinition[] = [
  {
    id: 'hints',
    label: 'Contextual hints',
    defaultEnabled: true,
    defaultPlacement: 'bottom',
    allowedPlacements: ['bottom'],
  },
  {
    id: 'usage',
    label: 'Usage reset timer',
    defaultEnabled: false,
    defaultPlacement: 'top',
    allowedPlacements: ['top', 'bottom'],
  },
  {
    id: 'workspace',
    label: 'Workspace indicator',
    defaultEnabled: true,
    defaultPlacement: 'top',
    allowedPlacements: ['top', 'bottom'],
  },
] as const;

const DEFINITION_BY_ID: Readonly<Record<BarWidgetId, BarWidgetDefinition>> = Object.fromEntries(
  BAR_WIDGET_DEFINITIONS.map((def) => [def.id, def]),
) as Record<BarWidgetId, BarWidgetDefinition>;

export function barWidgetDefinition(id: BarWidgetId): BarWidgetDefinition {
  return DEFINITION_BY_ID[id];
}

/** Merge persisted config onto registry defaults for one widget. */
export function resolveBarWidgetConfig(
  id: BarWidgetId,
  stored: BarWidgetsConfig | undefined,
): BarWidgetUserConfig {
  const def = barWidgetDefinition(id);
  const patch = stored?.[id];
  const placement =
    patch?.placement !== undefined && def.allowedPlacements.includes(patch.placement)
      ? patch.placement
      : def.defaultPlacement;
  const harnesses = patch?.harnesses;
  return {
    enabled: patch?.enabled ?? def.defaultEnabled,
    placement,
    adaptive: patch?.adaptive ?? true,
    ...(harnesses !== undefined && harnesses.length > 0 ? { harnesses: [...harnesses] } : {}),
  };
}

/** Resolved config for every registered widget. */
export function resolveAllBarWidgetConfigs(
  stored: BarWidgetsConfig | undefined,
): Readonly<Record<BarWidgetId, BarWidgetUserConfig>> {
  return Object.fromEntries(
    BAR_WIDGET_DEFINITIONS.map((def) => [def.id, resolveBarWidgetConfig(def.id, stored)]),
  ) as Record<BarWidgetId, BarWidgetUserConfig>;
}

/** Widget ids enabled for a placement, in registry order. */
export function enabledBarWidgetIds(
  stored: BarWidgetsConfig | undefined,
  placement: BarPlacement,
): readonly BarWidgetId[] {
  const resolved = resolveAllBarWidgetConfigs(stored);
  return BAR_WIDGET_DEFINITIONS.filter(
    (def) => resolved[def.id].enabled && resolved[def.id].placement === placement,
  ).map((def) => def.id);
}
