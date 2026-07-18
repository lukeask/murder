/**
 * spawnFavoritesActions tests — the save→load round-trip over the bus, plus the intentional
 * load-vs-save error asymmetry (load swallows → [], save propagates).
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import {
  createSpawnFavoritesActions,
  type SpawnFavorite,
} from '../../../src/store/dialogs/spawnFavoritesActions.js';

const FAVORITES: SpawnFavorite[] = [
  { name: 'OpusMed', harness: 'claude_code', model: 'opus', effort: 'medium' },
  { name: 'CodexHigh', harness: 'codex', model: 'gpt-5.5', effort: 'high' },
];

describe('spawnFavoritesActions', () => {
  it('save() sends the records and load() round-trips the persisted list', async () => {
    const bus = new FakeBusClient();
    // The save reply echoes back what was persisted; load returns that same list.
    bus.stubCommand('spawn_favorites.set', (p) => ({ ok: true, favorites: p.favorites }));
    bus.stubQuery('spawn_favorites.get', { ok: true, favorites: FAVORITES });
    const actions = createSpawnFavoritesActions(bus);

    const saved = await actions.save(FAVORITES);
    expect(saved).toEqual(FAVORITES);
    const sendCall = bus.commandCalls.find((c) => c.name === 'spawn_favorites.set');
    expect((sendCall?.params as { favorites: unknown }).favorites).toEqual(FAVORITES);

    expect(await actions.load()).toEqual(FAVORITES);
  });

  it('load() degrades to [] when the RPC rejects (unstubbed)', async () => {
    const bus = new FakeBusClient(); // no stub → rpc rejects
    expect(await createSpawnFavoritesActions(bus).load()).toEqual([]);
  });
});
