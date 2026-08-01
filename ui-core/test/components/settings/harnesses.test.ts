import { describe, expect, it } from 'vitest';
import {
  crowControlBackendDetailRows,
  HARNESS_ITEMS,
  startupRogueDetailRows,
} from '@murder/ui-core/components/settings/items/harnesses.js';
import type { SettingsBuildContext } from '@murder/ui-core/components/settings/types.js';

const CONTEXT: SettingsBuildContext = {
  llm: {},
  startupRogue: { harness: 'cursor', model: 'gpt-5', effort: 'medium' },
  startupRogueModels: {
    cursor: [
      { id: 'gpt-5', label: 'GPT-5' },
      { id: 'gpt-5.1', label: 'GPT-5.1' },
    ],
  },
  startupRogueEfforts: { cursor: ['low', 'medium', 'high'] },
  templates: [],
  themes: [],
  barWidgets: {},
  codexControlBackend: 'harness_parse',
  cursorControlBackend: 'harness_parse',
  claudeControlBackend: 'harness_parse',
};

describe('harnesses T2/T3 row builders', () => {
  it('flat T2 rows omit model/effort and control-backend sections', () => {
    const rows = HARNESS_ITEMS.flatMap((item) => item.rows(CONTEXT));
    expect(rows.some((r) => r.kind === 'header' && r.label === 'Startup Rogue')).toBe(true);
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'off')).toBe(true);
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'harness')).toBe(true);
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'model')).toBe(false);
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'effort')).toBe(false);
    expect(rows.some((r) => r.kind === 'codexControlBackend')).toBe(false);
    expect(rows.some((r) => r.kind === 'cursorControlBackend')).toBe(false);
    expect(rows.some((r) => r.kind === 'claudeControlBackend')).toBe(false);
    expect(rows.some((r) => r.kind === 'header' && r.label.includes('Control Backend'))).toBe(
      false,
    );
  });

  it('startupRogueDetailRows returns model and effort rows for a harness', () => {
    const rows = startupRogueDetailRows(
      'cursor',
      CONTEXT.startupRogue,
      CONTEXT.startupRogueModels,
      CONTEXT.startupRogueEfforts,
    );
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'model')).toBe(true);
    expect(rows.some((r) => r.kind === 'startupRogue' && r.field === 'effort')).toBe(true);
  });

  it('crowControlBackendDetailRows returns backends only for applicable harnesses', () => {
    expect(
      crowControlBackendDetailRows('codex').some((r) => r.kind === 'codexControlBackend'),
    ).toBe(true);
    expect(
      crowControlBackendDetailRows('codex').filter((r) => r.kind === 'codexControlBackend'),
    ).toHaveLength(2);
    expect(
      crowControlBackendDetailRows('cursor').some((r) => r.kind === 'cursorControlBackend'),
    ).toBe(true);
    expect(
      crowControlBackendDetailRows('claude_code').some((r) => r.kind === 'claudeControlBackend'),
    ).toBe(true);
    expect(crowControlBackendDetailRows('pi')).toEqual([]);
    expect(crowControlBackendDetailRows(null)).toEqual([]);
  });
});
