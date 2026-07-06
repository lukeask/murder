import { describe, expect, it } from 'vitest';
import { STARTUP_ROGUE_MODELS } from '../../src/components/settings/items/harnesses.js';

describe('settings harness items', () => {
  it('offers cursor startup rogue models without waiting for live discovery', () => {
    expect(STARTUP_ROGUE_MODELS['cursor']).toEqual([
      { id: 'composer-2.5', label: 'Composer 2.5' },
      { id: 'auto', label: 'Auto' },
      { id: 'gpt-5.5', label: 'GPT-5.5' },
      { id: 'gpt-5.4', label: 'GPT-5.4' },
      { id: 'claude-sonnet-4.5', label: 'Claude Sonnet 4.5' },
    ]);
  });
});
