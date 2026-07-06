import type { StartupRogueWire } from '../../../store/settings/settingsActions.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const HARNESSES: readonly string[] = ['claude_code', 'codex', 'cursor', 'pi', 'antigravity'];

export const STARTUP_ROGUE_MODELS: Readonly<Record<string, readonly string[]>> = {
  claude_code: ['', 'opus', 'sonnet', 'haiku', 'fable'],
  codex: ['', 'gpt-5.5', 'gpt-5.4', 'gpt-5.3-codex', 'gpt-5.2'],
  cursor: ['', 'composer-2.5', 'auto', 'gpt-5.5', 'gpt-5.4', 'claude-sonnet-4.5'],
  pi: [''],
  antigravity: [''],
};

export const STARTUP_ROGUE_EFFORTS: Readonly<Record<string, readonly string[]>> = {
  claude_code: ['low', 'medium', 'high', 'xhigh', 'max'],
  codex: ['low', 'medium', 'high', 'xhigh'],
  cursor: [],
  pi: [],
  antigravity: [],
};

export function defaultEffortFor(harness: string): string | null {
  const efforts = STARTUP_ROGUE_EFFORTS[harness] ?? [];
  if (efforts.length === 0) {
    return null;
  }
  return efforts.includes('medium') ? 'medium' : (efforts[0] ?? null);
}

const startupRogueItem: SettingsItem = {
  id: 'harnesses.startupRogue',
  label: 'Startup Rogue',
  rows: ({ startupRogue }) => {
    const rows: SettingsRow[] = [
      headerRow(startupRogueItem),
      { id: 'harnesses.startupRogue:off', kind: 'startupRogue', field: 'off' },
      ...HARNESSES.map(
        (value): SettingsRow => ({
          id: `harnesses.startupRogue:harness:${value}`,
          kind: 'startupRogue',
          field: 'harness',
          value,
        }),
      ),
    ];
    if (startupRogue !== null) {
      appendStartupRogueDetails(rows, startupRogue);
    }
    return rows;
  },
};

function appendStartupRogueDetails(rows: SettingsRow[], startupRogue: StartupRogueWire): void {
  for (const value of STARTUP_ROGUE_MODELS[startupRogue.harness] ?? ['']) {
    rows.push({
      id: `harnesses.startupRogue:model:${value || 'default'}`,
      kind: 'startupRogue',
      field: 'model',
      value,
    });
  }
  for (const value of STARTUP_ROGUE_EFFORTS[startupRogue.harness] ?? []) {
    rows.push({
      id: `harnesses.startupRogue:effort:${value}`,
      kind: 'startupRogue',
      field: 'effort',
      value,
    });
  }
}

const plannerItem: SettingsItem = {
  id: 'harnesses.planner',
  label: 'Planning Agent Harness',
  rows: () => [
    headerRow(plannerItem),
    { id: 'harnesses.planner:default', kind: 'planner', value: null },
    ...HARNESSES.map(
      (value): SettingsRow => ({ id: `harnesses.planner:${value}`, kind: 'planner', value }),
    ),
  ],
};

const crowItem: SettingsItem = {
  id: 'harnesses.crow',
  label: 'Crow Harnesses',
  rows: () => [
    headerRow(crowItem),
    { id: 'harnesses.crow:default', kind: 'crow', value: null },
    ...HARNESSES.map(
      (value): SettingsRow => ({ id: `harnesses.crow:${value}`, kind: 'crow', value }),
    ),
  ],
};

export const HARNESS_ITEMS: readonly SettingsItem[] = [startupRogueItem, plannerItem, crowItem];
