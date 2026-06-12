import { describe, expect, it } from 'vitest';
import {
  type ConnectionStatus,
  createConnectionStore,
} from '../../../src/store/connection/connectionStore.js';

// The connection store is a tiny vanilla-Zustand fact store driven by the index.tsx transport
// wiring. These tests use the factory for an isolated instance per case (mirroring the toast/caps
// store factory pattern) and assert the state transitions the wiring drives.

describe('connectionStore — factory + transitions', () => {
  it('starts in unknown by default (no badge state)', () => {
    const store = createConnectionStore();
    expect(store.getState().status).toBe('unknown');
  });

  it('honours a seeded initial status', () => {
    const store = createConnectionStore('connected');
    expect(store.getState().status).toBe('connected');
  });

  it('setStatus records each transport status', () => {
    const store = createConnectionStore();
    const sequence: ConnectionStatus[] = [
      'connecting',
      'connected',
      'reconnecting',
      'version-mismatch',
    ];
    for (const status of sequence) {
      store.getState().setStatus(status);
      expect(store.getState().status).toBe(status);
    }
  });

  it('notifies subscribers on a status change', () => {
    const store = createConnectionStore();
    const seen: ConnectionStatus[] = [];
    const unsubscribe = store.subscribe((state) => seen.push(state.status));
    store.getState().setStatus('connecting');
    store.getState().setStatus('connected');
    unsubscribe();
    store.getState().setStatus('reconnecting');
    expect(seen).toEqual(['connecting', 'connected']);
  });
});
