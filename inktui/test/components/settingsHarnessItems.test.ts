import { describe, expect, it } from 'vitest';
import { STARTUP_ROGUE_MODELS } from '../../src/components/settings/items/harnesses.js';

describe('settings harness items', () => {
  it('offers cursor startup rogue models without waiting for live discovery', () => {
    expect(STARTUP_ROGUE_MODELS['cursor']).toEqual([
      '',
      'composer-2.5',
      'auto',
      'gpt-5.5',
      'gpt-5.4',
      'claude-sonnet-4.5',
    ]);
  });
});
