/**
 * `keyUsageStore` tests — recency-decayed binding usage counts.
 */

import { describe, expect, it } from 'vitest';
import {
  createKeyUsageStore,
  decayedCount,
  KEY_USAGE_HALF_LIFE_MS,
  type KeyUsageRecord,
} from '../../src/store/keyUsage/keyUsageStore.js';

describe('keyUsageStore.recordUse (cookbook)', () => {
  it('creates a record on first use', () => {
    const store = createKeyUsageStore();
    const now = 1_000_000;
    store.getState().recordUse('global.spawn', now);
    expect(store.getState().actions['global.spawn']).toEqual({ count: 1, lastAt: now });
  });

  it('increments on a second use at the same instant', () => {
    const store = createKeyUsageStore();
    const now = 1_000_000;
    store.getState().recordUse('global.spawn', now);
    store.getState().recordUse('global.spawn', now);
    expect(store.getState().actions['global.spawn']).toEqual({ count: 2, lastAt: now });
  });

  it('decayedCount halves the count at exactly one half-life', () => {
    const record: KeyUsageRecord = { count: 4, lastAt: 0 };
    const now = KEY_USAGE_HALF_LIFE_MS;
    expect(decayedCount(record, now)).toBe(2);
  });

  it('hydrate replaces state wholesale', () => {
    const store = createKeyUsageStore();
    store.getState().recordUse('global.spawn', 100);
    const replacement = { 'plans:star': { count: 7, lastAt: 500 } };
    store.getState().hydrate(replacement);
    expect(store.getState().actions).toEqual(replacement);
  });
});

describe('keyUsageStore.recordUse (edge)', () => {
  it('after long idle, decayed old count is ~0 then +1', () => {
    const store = createKeyUsageStore();
    const t0 = 0;
    const t1 = 10 * KEY_USAGE_HALF_LIFE_MS;
    store.getState().recordUse('global.spawn', t0);
    store.getState().recordUse('global.spawn', t1);
    const record = store.getState().actions['global.spawn'];
    expect(record?.lastAt).toBe(t1);
    expect(record?.count).toBeCloseTo(1, 2);
  });
});
