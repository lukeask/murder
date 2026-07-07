/**
 * `keyUsagePersistence` tests — load/save round-trip and defensive parsing.
 */

import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  loadKeyUsage,
  startKeyUsagePersistence,
} from '../../src/store/keyUsage/keyUsagePersistence.js';
import { createKeyUsageStore } from '../../src/store/keyUsage/keyUsageStore.js';

let tempDir: string;

beforeEach(async () => {
  tempDir = await mkdtemp(path.join(tmpdir(), 'inktui-key-usage-'));
});

afterEach(async () => {
  await rm(tempDir, { recursive: true, force: true });
});

function usagePath(name = 'key_usage.json'): string {
  return path.join(tempDir, name);
}

describe('keyUsagePersistence (cookbook)', () => {
  it('round-trips record → cleanup flush → load', () => {
    const store = createKeyUsageStore();
    const filePath = usagePath();
    const stop = startKeyUsagePersistence(store, filePath);

    store.getState().recordUse('global.spawn', 1_000_000);
    store.getState().recordUse('plans:star', 2_000_000);

    stop();

    expect(loadKeyUsage(filePath)).toEqual({
      'global.spawn': { count: 1, lastAt: 1_000_000 },
      'plans:star': { count: 1, lastAt: 2_000_000 },
    });
  });

  it('load of a missing file returns {}', () => {
    expect(loadKeyUsage(usagePath('missing.json'))).toEqual({});
  });
});

describe('keyUsagePersistence (edge)', () => {
  it('corrupt JSON returns {}', async () => {
    const filePath = usagePath();
    await writeFile(filePath, '{not json', 'utf8');
    expect(loadKeyUsage(filePath)).toEqual({});
  });

  it('drops invalid entries and keeps valid ones', async () => {
    const filePath = usagePath();
    await writeFile(
      filePath,
      JSON.stringify({
        good: { count: 3, lastAt: 100 },
        badCount: { count: 'nope', lastAt: 200 },
        badLastAt: { count: 1, lastAt: null },
        missing: { count: 1 },
      }),
      'utf8',
    );
    expect(loadKeyUsage(filePath)).toEqual({
      good: { count: 3, lastAt: 100 },
    });
  });

  it('caps to the 200 entries with the highest lastAt', async () => {
    const filePath = usagePath();
    const actions: Record<string, { count: number; lastAt: number }> = {};
    for (let i = 0; i < 250; i++) {
      actions[`action-${i}`] = { count: 1, lastAt: i };
    }
    await writeFile(filePath, JSON.stringify(actions), 'utf8');

    const loaded = loadKeyUsage(filePath);
    expect(Object.keys(loaded)).toHaveLength(200);
    expect(loaded['action-249']).toEqual({ count: 1, lastAt: 249 });
    expect(loaded['action-0']).toBeUndefined();
    expect(loaded['action-49']).toBeUndefined();
    expect(loaded['action-50']).toEqual({ count: 1, lastAt: 50 });
  });
});
