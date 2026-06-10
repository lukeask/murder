/**
 * harnessModelsActions tests — the pull-only `state.harness_models_snapshot` RPC + static fallback.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import {
  createHarnessModelsActions,
  modelsFor,
  STATIC_HARNESS_MODELS,
} from '../../../src/store/dialogs/harnessModelsActions.js';

describe('harnessModelsActions — fetch', () => {
  it('returns the live snapshot models (merged over the static map)', async () => {
    const bus = new FakeBusClient();
    bus.stubRpc('state.harness_models_snapshot', {
      models: { claude_code: [{ id: 'opus', label: 'Opus 5' }] },
      as_of: '2026-06-09T00:00:00Z',
    });
    const map = await createHarnessModelsActions(bus).fetch();
    expect(map['claude_code']).toEqual([{ id: 'opus', label: 'Opus 5' }]);
    // A harness the snapshot omits still keeps its static last-good list.
    expect(map['codex']).toEqual(STATIC_HARNESS_MODELS['codex']);
  });

  it('falls back to the static map when the RPC is not live (rejects)', async () => {
    const bus = new FakeBusClient(); // no stub → rpc rejects
    const map = await createHarnessModelsActions(bus).fetch();
    expect(map).toBe(STATIC_HARNESS_MODELS);
  });
});

describe('harnessModelsActions — modelsFor (pure)', () => {
  it('indexes the map per harness', () => {
    expect(modelsFor('claude_code', STATIC_HARNESS_MODELS).map((m) => m.id)).toEqual([
      'sonnet',
      'opus',
      'haiku',
    ]);
  });

  it('returns [] for a missing or empty harness key', () => {
    expect(modelsFor('cursor', STATIC_HARNESS_MODELS)).toEqual([]);
    expect(modelsFor('bogus', STATIC_HARNESS_MODELS)).toEqual([]);
  });
});
