/**
 * `kittyUserVar` — OSC 1337 `SetUserVar` helpers for kitty focused-window conditionals.
 *
 * Emits sequences only when `KITTY_WINDOW_ID` is set (kitty sets this for child processes).
 * Other terminals never see these bytes.
 */

/** Emit kitty's `SetUserVar` OSC 1337 sequence. `value === null` unsets the variable. */
export function setKittyUserVar(name: string, value: string | null): void {
  if (!process.env.KITTY_WINDOW_ID) {
    return;
  }

  if (value === null) {
    process.stdout.write(`\x1b]1337;SetUserVar=${name}\x07`);
  } else {
    const encoded = Buffer.from(value).toString('base64');
    process.stdout.write(`\x1b]1337;SetUserVar=${name}=${encoded}\x07`);
  }
}

const CLEANUP_SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP'] as const;

let cleanupInstalled = false;

/** Register process-level cleanup so `murder_tui` is unset on exit and common signals. Idempotent. */
export function ensureKittyMurderMarkerCleanup(): void {
  if (!process.env.KITTY_WINDOW_ID || cleanupInstalled) {
    return;
  }
  cleanupInstalled = true;

  const unset = (): void => {
    setKittyUserVar('murder_tui', null);
  };

  process.on('exit', unset);

  for (const signal of CLEANUP_SIGNALS) {
    const handler = (): void => {
      unset();
      process.removeListener(signal, handler);
      process.kill(process.pid, signal);
    };
    process.on(signal, handler);
  }
}
