import { describe, expect, it } from 'vitest';
import { canLaunchBuiltinTicket } from '../../../src/store/dialogs/canLaunchBuiltinTicket.js';
import { initialSettingsState } from '../../../src/store/settings/settingsSlice.js';

describe('canLaunchBuiltinTicket', () => {
  it('is false when no startup rogue is configured', () => {
    expect(canLaunchBuiltinTicket(initialSettingsState)).toBe(false);
  });

  it('is true when startup rogue has harness + model', () => {
    expect(
      canLaunchBuiltinTicket({
        ...initialSettingsState,
        startupRogue: { harness: 'codex', model: 'gpt-5', effort: null },
      }),
    ).toBe(true);
  });

  it('resolves an empty model from the harness catalog default', () => {
    expect(
      canLaunchBuiltinTicket({
        ...initialSettingsState,
        startupRogue: { harness: 'codex', model: '', effort: null },
      }),
    ).toBe(true);
  });
});
