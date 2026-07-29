import type {
  StartupRogueModelWire,
  StartupRogueWire,
} from '../../../store/settings/settingsActions.js';
import {
  DEFAULT_STARTUP_ROGUE_EFFORTS,
  DEFAULT_STARTUP_ROGUE_MODELS,
} from '../../../store/settings/settingsSlice.js';
import { effortMatrixFor } from '../../spawnWizardMachine.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const HARNESSES: readonly string[] = ['claude_code', 'codex', 'cursor', 'pi', 'antigravity'];

export const STARTUP_ROGUE_MODELS = DEFAULT_STARTUP_ROGUE_MODELS;

export const STARTUP_ROGUE_EFFORTS = DEFAULT_STARTUP_ROGUE_EFFORTS;

export function startupRogueModelsFor(
  harness: string,
  models: Readonly<Record<string, readonly StartupRogueModelWire[]>>,
): readonly StartupRogueModelWire[] {
  const choices = models[harness] ?? STARTUP_ROGUE_MODELS[harness] ?? [];
  return choices.length > 0 ? choices : [{ id: '', label: '(default model)' }];
}

export function defaultModelFor(
  harness: string,
  models: Readonly<Record<string, readonly StartupRogueModelWire[]>>,
): string {
  return startupRogueModelsFor(harness, models)[0]?.id ?? '';
}

export function startupRogueEffortsFor(
  harness: string,
  effortsByHarness: Readonly<Record<string, readonly string[]>>,
  model = '',
): readonly string[] {
  if (harness === 'cursor') {
    return effortMatrixFor(harness, model).options;
  }
  return effortsByHarness[harness] ?? STARTUP_ROGUE_EFFORTS[harness] ?? [];
}

export function defaultEffortFor(
  harness: string,
  effortsByHarness: Readonly<Record<string, readonly string[]>> = STARTUP_ROGUE_EFFORTS,
  model = '',
): string | null {
  const efforts = startupRogueEffortsFor(harness, effortsByHarness, model);
  if (efforts.length === 0) {
    return null;
  }
  return efforts.includes('medium') ? 'medium' : (efforts[0] ?? null);
}

const startupRogueItem: SettingsItem = {
  id: 'harnesses.startupRogue',
  label: 'Startup Rogue',
  rows: () => [
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
  ],
};

/** Model + effort rows for a startup-rogue harness (T3 detail column). */
export function startupRogueDetailRows(
  harness: string,
  startupRogue: StartupRogueWire | null,
  modelsByHarness: Readonly<Record<string, readonly StartupRogueModelWire[]>>,
  effortsByHarness: Readonly<Record<string, readonly string[]>>,
): readonly SettingsRow[] {
  const model =
    startupRogue?.harness === harness
      ? startupRogue.model
      : defaultModelFor(harness, modelsByHarness);
  const rows: SettingsRow[] = [];
  appendStartupRogueDetails(rows, harness, model, modelsByHarness, effortsByHarness);
  return rows;
}

function appendStartupRogueDetails(
  rows: SettingsRow[],
  harness: string,
  model: string,
  modelsByHarness: Readonly<Record<string, readonly StartupRogueModelWire[]>>,
  effortsByHarness: Readonly<Record<string, readonly string[]>>,
): void {
  for (const entry of startupRogueModelsFor(harness, modelsByHarness)) {
    rows.push({
      id: `harnesses.startupRogue:model:${entry.id || 'default'}`,
      kind: 'startupRogue',
      field: 'model',
      value: entry.id,
      label: entry.label,
    });
  }
  for (const value of startupRogueEffortsFor(harness, effortsByHarness, model)) {
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

const CODEX_CONTROL_BACKENDS = ['harness_parse', 'app_server'] as const;
const CURSOR_CONTROL_BACKENDS = ['harness_parse', 'acp'] as const;
const CLAUDE_CONTROL_BACKENDS = ['harness_parse', 'agent_sdk'] as const;

const codexControlBackendItem: SettingsItem = {
  id: 'harnesses.codexControlBackend',
  label: 'Codex Control Backend',
  rows: () => [],
};

const cursorControlBackendItem: SettingsItem = {
  id: 'harnesses.cursorControlBackend',
  label: 'Cursor Control Backend',
  rows: () => [],
};

const claudeControlBackendItem: SettingsItem = {
  id: 'harnesses.claudeControlBackend',
  label: 'Claude Control Backend',
  rows: () => [],
};

/** Control-backend radios for a crow harness that has one; empty if none. */
export function crowControlBackendDetailRows(crowHarness: string | null): readonly SettingsRow[] {
  switch (crowHarness) {
    case 'codex':
      return [
        headerRow(codexControlBackendItem),
        ...CODEX_CONTROL_BACKENDS.map(
          (value): SettingsRow => ({
            id: `harnesses.codexControlBackend:${value}`,
            kind: 'codexControlBackend',
            value,
          }),
        ),
      ];
    case 'cursor':
      return [
        headerRow(cursorControlBackendItem),
        ...CURSOR_CONTROL_BACKENDS.map(
          (value): SettingsRow => ({
            id: `harnesses.cursorControlBackend:${value}`,
            kind: 'cursorControlBackend',
            value,
          }),
        ),
      ];
    case 'claude_code':
      return [
        headerRow(claudeControlBackendItem),
        ...CLAUDE_CONTROL_BACKENDS.map(
          (value): SettingsRow => ({
            id: `harnesses.claudeControlBackend:${value}`,
            kind: 'claudeControlBackend',
            value,
          }),
        ),
      ];
    default:
      return [];
  }
}

export const HARNESS_ITEMS: readonly SettingsItem[] = [startupRogueItem, plannerItem, crowItem];
