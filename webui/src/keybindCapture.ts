/**
 * Keybind capture validation — shared by SettingsPanel capture-to-rebind and its tests.
 * Mirrors TUI SettingsModal rejection rules (reserved digits/ctrl letters + collision).
 */

import {
  REBINDABLE,
  RESERVED_KEYS,
} from '@murder/ui-core/components/settings/items/keybindings.js';
import { ACTIONS, type ActionId } from '@murder/ui-core/input/bindings.js';

export type CaptureValidateResult =
  | { readonly ok: true; readonly key: string }
  | { readonly ok: false; readonly notice: string };

function actionDefaultKey(action: ActionId): string {
  const def = ACTIONS[action].default;
  return def.kind === 'command' ? def.key : '';
}

/** Validate a captured printable key for rebinding `action` into `overrides`. */
export function validateRebindCapture(
  action: ActionId,
  raw: string,
  overrides: Readonly<Record<string, string>>,
): CaptureValidateResult {
  if (raw.length !== 1) {
    return { ok: false, notice: 'Press a single printable key (Esc to cancel)' };
  }
  const lower = raw.toLowerCase();
  const display = lower === ' ' ? 'space' : lower;
  if (RESERVED_KEYS.has(lower)) {
    return { ok: false, notice: `"${display}" is reserved and cannot be rebound` };
  }
  const collision = REBINDABLE.find((other) => {
    if (other === action) {
      return false;
    }
    const otherKey = overrides[other] ?? actionDefaultKey(other);
    return otherKey === lower;
  });
  if (collision !== undefined) {
    return {
      ok: false,
      notice: `"${display}" is already bound to "${ACTIONS[collision].description}"`,
    };
  }
  return { ok: true, key: lower };
}

/** Label for a bound key char (space → "space"). */
export function bindingKeyLabel(key: string, fallbackDefault: string): string {
  if (key === '') {
    return fallbackDefault === ' ' ? 'space' : fallbackDefault || '—';
  }
  return key === ' ' ? 'space' : key;
}
