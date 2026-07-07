import { describe, expect, it } from 'vitest';
import { BAR_ITEMS } from '../../../src/components/settings/items/bars.js';
import {
  BAR_WIDGET_DEFINITIONS,
  resolveBarWidgetConfig,
} from '../../../src/selectors/barWidgetRegistry.js';

describe('bar widget registry', () => {
  it('usage widget defaults to disabled on the top bar', () => {
    const def = BAR_WIDGET_DEFINITIONS.find((entry) => entry.id === 'usage');
    expect(def).toMatchObject({ defaultEnabled: false, defaultPlacement: 'top' });
    expect(resolveBarWidgetConfig('usage', undefined)).toMatchObject({
      enabled: false,
      placement: 'top',
    });
  });

  it('resolveBarWidgetConfig round-trips harness selection for usage', () => {
    expect(
      resolveBarWidgetConfig('usage', {
        usage: { enabled: true, placement: 'top', harnesses: ['codex'] },
      }),
    ).toMatchObject({ harnesses: ['codex'] });
    expect(
      resolveBarWidgetConfig('usage', {
        usage: { enabled: true, placement: 'top', harnesses: [] },
      }).harnesses,
    ).toBeUndefined();
  });
});

describe('Bars settings rows', () => {
  it('includes harness checkboxes for the usage widget', () => {
    const usageItem = BAR_ITEMS.find((item) => item.id === 'bars.usage');
    expect(usageItem).toBeDefined();
    const rows = usageItem?.rows({
      llm: {},
      startupRogue: null,
      startupRogueModels: {},
      startupRogueEfforts: {},
      templates: [],
      themes: [],
      barWidgets: {},
    });
    const harnessRows = rows?.filter((row) => row.kind === 'barWidgetHarness') ?? [];
    expect(harnessRows).toHaveLength(5);
    expect(harnessRows.map((row) => (row.kind === 'barWidgetHarness' ? row.value : ''))).toContain(
      'claude_code',
    );
  });
});
