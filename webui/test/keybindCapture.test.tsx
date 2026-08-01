/** keybindCapture + SettingsPanel capture-to-rebind tests. */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { validateRebindCapture } from '../src/keybindCapture.js';
import { makeStore, renderWithStore, stubSettingsUpdate } from './helpers.js';

afterEach(cleanup);

function Harness(): React.JSX.Element {
  return <SettingsPanel />;
}

describe('validateRebindCapture', () => {
  it('accepts a clean printable key', () => {
    expect(validateRebindCapture('global.spawn', 'q', {})).toEqual({ ok: true, key: 'q' });
  });

  it('rejects reserved digits and ctrl letters', () => {
    expect(validateRebindCapture('global.spawn', '3', {}).ok).toBe(false);
    expect(validateRebindCapture('global.spawn', 'c', {}).ok).toBe(false);
  });

  it('rejects a collision with another rebindable default', () => {
    const result = validateRebindCapture('global.spawn', 't', {});
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.notice).toContain('already bound');
    }
  });
});

describe('SettingsPanel capture-to-rebind', () => {
  it('persists key_overrides when a rebindable action key is captured', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'rebind spawn' }));
    fireEvent.keyDown(document, { key: 'q' });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { key_overrides: { 'global.spawn': 'q' } } },
    });
  });

  it('rejects a reserved capture without writing key_overrides', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'rebind spawn' }));
    fireEvent.keyDown(document, { key: '3' });

    expect(bus.commandCalls.some((c) => c.name === 'settings.update' && 'key_overrides' in (c.params.settings ?? {}))).toBe(
      false,
    );
    expect(screen.getByText(/reserved/i)).toBeTruthy();
  });

  it('cancels capture on Escape without writing', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'rebind spawn' }));
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(bus.commandCalls.some((c) => c.name === 'settings.update')).toBe(false);
    expect(screen.getByText(/Click Rebind/i)).toBeTruthy();
  });
});
