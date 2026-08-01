import { afterEach, describe, expect, it, vi } from 'vitest';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { ticketEditorMode } from '../../src/components/TicketEditorMode.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { makeKey } from '../input/key.js';

const disposers: Array<() => void> = [];

afterEach(() => {
  for (const dispose of disposers.splice(0)) dispose();
});

function setup() {
  const stores = createInputStores(['workflows'], 'workflows');
  const { store, dispose } = createAppStore(new FakeApplicationClient());
  disposers.push(dispose);
  const onSave = vi.fn();
  const onDiscard = vi.fn();
  const mode = ticketEditorMode(stores.modes, store, { onSave, onDiscard });
  stores.modes.getState().enter(mode);
  return { stores, mode, onSave, onDiscard };
}

describe('TicketEditorMode exit behavior', () => {
  it('closes and discards on Escape from normal mode', () => {
    const { stores, mode, onDiscard } = setup();

    mode.onIntent('escape');

    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(onDiscard).toHaveBeenCalledOnce();
  });

  it('uses Escape to leave insert mode before a second Escape closes the editor', () => {
    const { stores, mode, onDiscard } = setup();
    mode.onUncaptured?.('i', makeKey());

    mode.onIntent('escape');
    expect(selectActiveMode(stores.modes)?.id).toBe(mode.id);
    expect(onDiscard).not.toHaveBeenCalled();

    mode.onIntent('escape');
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(onDiscard).toHaveBeenCalledOnce();
  });

  it('advertises the normal-mode exit in the mode hint bar', () => {
    const { mode } = setup();

    expect(mode.hints).toContainEqual({
      key: 'esc/q',
      description: 'discard & close',
    });
  });
});
