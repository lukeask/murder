/**
 * Map a browser KeyboardEvent to VT/xterm raw stdin bytes.
 * Closest practical match to what a real TTY would emit for ordinary keys;
 * kitty CSI-u / modifyOtherKeys are not synthesized (TUI raw route also runs without them).
 */

const encoder = new TextEncoder();

const ARROWS: Readonly<Record<string, string>> = {
  ArrowUp: 'A',
  ArrowDown: 'B',
  ArrowRight: 'C',
  ArrowLeft: 'D',
};

const NAV_TILDE: Readonly<Record<string, number>> = {
  Insert: 2,
  Delete: 3,
  PageUp: 5,
  PageDown: 6,
  Home: 1,
  End: 4,
};

const FUNCTION_KEYS: Readonly<Record<string, string>> = {
  F1: '\x1bOP',
  F2: '\x1bOQ',
  F3: '\x1bOR',
  F4: '\x1bOS',
  F5: '\x1b[15~',
  F6: '\x1b[17~',
  F7: '\x1b[18~',
  F8: '\x1b[19~',
  F9: '\x1b[20~',
  F10: '\x1b[21~',
  F11: '\x1b[23~',
  F12: '\x1b[24~',
};

function modifierParam(event: KeyboardEvent): number {
  // xterm/CSI: 1 + shift + 2*alt + 4*ctrl (meta omitted — browser chord territory).
  return 1 + (event.shiftKey ? 1 : 0) + (event.altKey ? 2 : 0) + (event.ctrlKey ? 4 : 0);
}

function csiModified(final: string, mod: number): Uint8Array {
  if (mod <= 1) return encoder.encode(`\x1b[${final}`);
  return encoder.encode(`\x1b[1;${mod}${final}`);
}

function csiTilde(code: number, mod: number): Uint8Array {
  if (mod <= 1) return encoder.encode(`\x1b[${code}~`);
  return encoder.encode(`\x1b[${code};${mod}~`);
}

function ctrlLetter(key: string): number | null {
  if (key.length !== 1) return null;
  const lower = key.toLowerCase();
  const code = lower.charCodeAt(0);
  if (code >= 0x61 && code <= 0x7a) return code - 0x60;
  return null;
}

/**
 * Encode `event` for terminal stdin. Returns `null` when the key should pass through
 * to the browser (unrecognized, lone modifier, or Meta/Cmd chords).
 */
export function encodeTerminalKey(event: KeyboardEvent): Uint8Array | null {
  if (event.isComposing || event.key === 'Process') return null;
  if (event.key === 'Shift' || event.key === 'Control' || event.key === 'Alt' || event.key === 'Meta') {
    return null;
  }
  // Leave browser/OS chords alone (reload, copy from chrome, etc.).
  if (event.metaKey) return null;

  const { key } = event;

  if (key === 'Enter') return new Uint8Array([0x0d]);
  if (key === 'Tab') {
    return event.shiftKey ? encoder.encode('\x1b[Z') : new Uint8Array([0x09]);
  }
  if (key === 'Backspace') return new Uint8Array([0x7f]);
  if (key === 'Escape') return new Uint8Array([0x1b]);
  if (key === ' ') {
    if (event.ctrlKey) return new Uint8Array([0x00]);
    if (event.altKey) return encoder.encode('\x1b ');
    return new Uint8Array([0x20]);
  }

  const arrow = ARROWS[key];
  if (arrow !== undefined) {
    return csiModified(arrow, modifierParam(event));
  }

  const tildeCode = NAV_TILDE[key];
  if (tildeCode !== undefined) {
    // Prefer application-cursor Home/End when unmodified (common xterm default).
    if (key === 'Home' && modifierParam(event) <= 1) return encoder.encode('\x1b[H');
    if (key === 'End' && modifierParam(event) <= 1) return encoder.encode('\x1b[F');
    return csiTilde(tildeCode, modifierParam(event));
  }

  const fn = FUNCTION_KEYS[key];
  if (fn !== undefined) {
    if (modifierParam(event) <= 1) return encoder.encode(fn);
    // Modified function keys: CSI code;mod~
    const modifiedCodes: Readonly<Record<string, number>> = {
      F1: 11,
      F2: 12,
      F3: 13,
      F4: 14,
      F5: 15,
      F6: 17,
      F7: 18,
      F8: 19,
      F9: 20,
      F10: 21,
      F11: 23,
      F12: 24,
    };
    const code = modifiedCodes[key];
    if (code === undefined) return null;
    return csiTilde(code, modifierParam(event));
  }

  if (event.ctrlKey && !event.altKey) {
    const ctrl = ctrlLetter(key);
    if (ctrl !== null) return new Uint8Array([ctrl]);
    if (key === '@' || key === '2') return new Uint8Array([0x00]);
    if (key === '[') return new Uint8Array([0x1b]);
    if (key === '\\') return new Uint8Array([0x1c]);
    if (key === ']') return new Uint8Array([0x1d]);
    if (key === '^' || key === '6') return new Uint8Array([0x1e]);
    if (key === '_' || key === '-' || key === '/') return new Uint8Array([0x1f]);
    if (key === '?') return new Uint8Array([0x7f]);
    return null;
  }

  // Printable / Alt+printable. `key` is the produced character for ordinary typing.
  if (key.length === 1) {
    const bytes = encoder.encode(key);
    if (event.altKey && !event.ctrlKey) {
      const out = new Uint8Array(1 + bytes.length);
      out[0] = 0x1b;
      out.set(bytes, 1);
      return out;
    }
    return bytes;
  }

  return null;
}
