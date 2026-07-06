#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import { render } from 'ink';
import type { BusClient } from './bus/BusClient.js';
import { FakeBusClient } from './bus/FakeBusClient.js';
import { UdsBusClient } from './bus/UdsBusClient.js';
import { App } from './components/App.js';
import { createInputStores } from './input/createInputStores.js';
import type { PanelId } from './input/panels.js';
import { connectionStore } from './store/connection/connectionStore.js';
import { createAppStore } from './store/store.js';
import { capsStore } from './terminal/capsStore.js';
import { createKittyDriver, type KeyProtocolDriver } from './terminal/kittyDriver.js';
import { forceInkFullRepaint } from './terminal/forceInkRepaint.js';
import { StdinShim } from './terminal/StdinShim.js';

export { forceInkFullRepaint } from './terminal/forceInkRepaint.js';

/**
 * Process entrypoint — the **standing live runner** (F7). It constructs the injected stores and
 * renders the real app shell (rule 4: the bus is wired in exactly here, never imported by a
 * component), then holds the terminal open until the user exits.
 *
 * ## Two modes
 *
 *  - **Live (default, `node dist/index.js`).** Wires a {@link UdsBusClient} onto the bus socket given
 *    by `MURDER_BUS_SOCKET` (Open decision #2 — the Python launcher resolves the per-project socket
 *    path and hands it over via the env var; the TS side NEVER reimplements that hash, it only
 *    connects to the path it is given). The app stays mounted: Ink keeps the process alive on stdin
 *    raw mode, slice-invalidation events from the service repaint the panels live, and the run ends
 *    only when the user exits (ctrl+c → Ink resolves `waitUntilExit`). On exit we tear down the store
 *    subscriptions and close the socket.
 *
 *  - **Smoke (`--smoke`).** A one-shot mount→unmount against a {@link FakeBusClient}, requiring **no**
 *    socket and **no** running service. It paints one frame and exits clean, so the CI build gate
 *    (F8) can invoke `node index.js --smoke` to prove the bundle loads and parses. This is the old
 *    dev-smoke behaviour, preserved deliberately as the cheap "does it boot" check.
 *
 * `main()`/`runLive()`/`runSmoke()` are exported and the auto-invoke at the bottom is guarded to the
 * real entrypoint, so a test can import this module and drive each path without spawning a run.
 */

/** Panels seeded visible on startup — intentionally EMPTY. A fresh `murder` opens with no rail
 * panels at all: the only thing on screen is the chat input plus (once the daemon ensures the
 * user's configured Startup Rogue) that rogue's empty transcript pane in the Stage. Every rail
 * panel (plans, tickets, usage, crows, …) is opt-in via its `ctrl/alt+<n>` toggle. This keeps the
 * default landing focused on "type to your crow", building muscle memory for murder over claude.
 * The smoke build shares this constant: an empty set just means it boots with no rail panels. */
const STARTUP_PANELS: readonly PanelId[] = [];

/**
 * Read the bus socket path from `MURDER_BUS_SOCKET`. The Python launcher resolves the per-project
 * absolute path (Open decision #2) and passes it here; this side does not derive or rehash it. A
 * missing/empty var is a hard, clear failure — without it there is no service to connect to.
 */
export function resolveSocketPath(env: NodeJS.ProcessEnv = process.env): string {
  const socketPath = env['MURDER_BUS_SOCKET'];
  if (socketPath === undefined || socketPath.trim().length === 0) {
    throw new Error(
      'MURDER_BUS_SOCKET is not set. The murder launcher must pass the absolute bus socket path ' +
        'via this env var (the Ink runner does not derive the per-project socket path itself). ' +
        'Run the TUI via `murder`, or set MURDER_BUS_SOCKET to the bus socket path.',
    );
  }
  return socketPath;
}

/**
 * The current project/repo name for the top-bar branding, taken from `MURDER_PROJECT` (the launcher
 * sets it to the repo directory name — the TUI's own cwd is unreliable, since in dev it runs from
 * `inktui/`). Optional and purely cosmetic: a missing var just means the bar shows the bare `murder`
 * mark with no project suffix, so this never fails the run.
 */
export function resolveProject(env: NodeJS.ProcessEnv = process.env): string | undefined {
  const project = env['MURDER_PROJECT'];
  if (project === undefined || project.trim().length === 0) {
    return undefined;
  }
  return project.trim();
}

/**
 * Mount the shell against a live {@link UdsBusClient} and hold the terminal open until the user
 * exits. The store hydrates itself through the bus hydrate contract on construction: one snapshot
 * reply plus server-attached tails replaces the old startup prime RPCs and replay-gated
 * subscriptions. Returns when the app exits.
 */
export async function runLive(busFactory: () => BusClient = makeLiveBus): Promise<void> {
  const bus = busFactory();
  const { store, dispose } = createAppStore(bus);
  const inputStores = createInputStores(STARTUP_PANELS);

  // Connection-state badge wiring (mirrors the onConnect narrowing above). The transport drives the
  // process-global connectionStore; the TopBar reads it. We set `'connecting'` explicitly before the
  // first `connect()` (so the badge shows during the initial handshake), then let the hooks advance
  // it: onConnect → connected, onDisconnect → reconnecting, onPermanentError → version-mismatch
  // (the only permanent error today is a protocol-version mismatch, so it maps directly). A transport
  // that exposes no hooks (the fake) simply leaves the store at its set value.
  connectionStore.getState().setStatus('connecting');
  const unhookConnectedStatus = onConnectIfSupported(bus, () =>
    connectionStore.getState().setStatus('connected'),
  );
  const unhookDisconnect = onDisconnectIfSupported(bus, () =>
    connectionStore.getState().setStatus('reconnecting'),
  );
  const unhookPermanentError = onPermanentErrorIfSupported(bus, () =>
    connectionStore.getState().setStatus('version-mismatch'),
  );

  // Phase 2 — the kitty stdin shim. Constructed in BYPASS (pure passthrough) and handed to Ink as its
  // stdin, so until the protocol is actually enabled Ink sees the identical byte stream it always did
  // (behavior-neutral under the alt default). `terminalEvents = shim` carries the side-channel `chord`
  // events into the root input loop. Detection + enable/disable is driven post-render by
  // `setupTerminal` (the parser owns stdin, so the protocol replies never reach Ink).
  const shim = new StdinShim(process.stdin);

  // `alternateScreen: true` is the keystone for a full-screen TUI: Ink draws on the terminal's
  // alternate screen buffer (like vim/less/Textual), which has NO scrollback. Without it, Ink renders
  // inline and erases by counting lines — once the frame fills the terminal height, writing the bottom
  // line scrolls the viewport and Ink's next erase is off by a line, so every repaint leaves residue
  // and full frames stack into scrollback. The alternate screen removes that failure mode entirely
  // (and Ink also drops the trailing newline on fullscreen frames, avoiding the bottom-line scroll).
  // Ink restores the primary screen + cursor on unmount, so exit is clean.
  const instance = render(
    <App
      store={store}
      inputStores={inputStores}
      bus={bus}
      project={resolveProject()}
      terminalEvents={shim}
    />,
    {
      // The shim is a `Readable` that implements the stdin surface Ink actually uses (data events,
      // isTTY, setRawMode, ref/unref, resume/pause/setEncoding). Ink's option types it as the full
      // `NodeJS.ReadStream`; we provide the consumed subset, so cast through `unknown`.
      stdin: shim as unknown as NodeJS.ReadStream,
      alternateScreen: true,
      // The full-screen TUI repaints frequently while typing. Ink's default renderer erases the
      // previous full frame before writing the next one; on slower SSH links that erase can become a
      // visible blank flash. Incremental rendering rewrites only changed lines, which keeps stable
      // panes on-screen between keystrokes and avoids the distracting flicker.
      incrementalRendering: true,
    },
  );
  const teardownResizeClear = installResizeClear(process.stdout, () =>
    forceInkFullRepaint(process.stdout),
  );
  // Wire the protocol lifecycle through the shim now that it is Ink's stdin: detect support, feed
  // `ctrlAvailable`, and enable the protocol only when the user's modifier wants ctrl AND it is
  // supported. Returns a teardown that pops the protocol (best-effort).
  const teardownTerminal = await setupTerminal(shim, inputStores);
  // No unmount-on-tick here (that was the smoke scaffold): the app stays mounted. Ink keeps the
  // process alive and resolves `waitUntilExit` when the user exits (ctrl+c by default).
  try {
    await instance.waitUntilExit();
  } finally {
    teardownResizeClear();
    teardownTerminal();
    shim.dispose();
    unhookConnectedStatus?.();
    unhookDisconnect?.();
    unhookPermanentError?.();
    dispose();
    closeIfSupported(bus);
  }
}

/**
 * Wire the kitty stdin shim's protocol lifecycle (Phase 2). Run *after* `render` so the shim is
 * already Ink's stdin and detection's reply bytes are owned by the shim's parser (Ink never sees
 * them). Steps:
 *
 *  1. Build the kitty driver over `process.stdout` + the shim (its {@link StdinShim.subscribe} token
 *     source). Use the process-global {@link capsStore caps store}.
 *  2. `detect()` through the shim; record the result in the caps store AND the bindings store's
 *     `ctrlAvailable` (so `ctrl`/`both` degrade to alt when unsupported — see `resolveBindings`).
 *  3. Apply the current modifier: enable the protocol + leave bypass iff the modifier wants ctrl
 *     (`ctrl`/`both`) and it is supported; otherwise stay in bypass (behavior-neutral). Subscribe to
 *     the bindings store so a live settings change re-applies (alt → disable+bypass).
 *  4. Register best-effort `exit`/`SIGTERM` pops so a crash without the normal teardown does not leave
 *     the parent shell's protocol flags pushed (which would garble its input).
 *
 * Returns a teardown fn (idempotent disable + listener cleanup) for the `finally` path.
 */
export async function setupTerminal(
  shim: StdinShim,
  inputStores: ReturnType<typeof createInputStores>,
): Promise<() => void> {
  const caps = capsStore;
  const driver: KeyProtocolDriver = createKittyDriver(
    { write: (data) => process.stdout.write(data) },
    shim,
  );
  const bindings = inputStores.bindings;

  // SGR mouse reporting — INDEPENDENT of the kitty keyboard gate below. We enable it whenever stdout
  // is a real TTY so the mouse wheel sends SGR reports (`CSI < 64/65 ; x ; y M`) the shim lifts into
  // `wheel` events. Without this, kitty's alternate-scroll feature downgrades the wheel to Up/Down
  // arrow keys (which read as chat-history navigation in the input) — the bug this fixes. Mode 1000 is
  // button-press tracking only (no motion spam); 1006 is the SGR extended-coordinate encoding. The
  // tradeoff: the terminal hands click-drag to us, so native text selection needs Shift+drag.
  const MOUSE_ON = '\x1b[?1000h\x1b[?1006h';
  const MOUSE_OFF = '\x1b[?1006l\x1b[?1000l';
  const mouseCapable = shim.isTTY === true && process.stdout.isTTY === true;
  let mouseEnabled = false;
  if (mouseCapable) {
    process.stdout.write(MOUSE_ON);
    shim.setMouseEnabled(true);
    mouseEnabled = true;
  }

  // Detect (through the shim). A non-answering terminal resolves false on the driver's timeout.
  const supported = await detectIfTty(shim, driver);
  caps.getState().setKittySupported(supported);
  bindings.getState().setCtrlAvailable(supported);

  // Apply the protocol state for a given modifier: enable + active iff ctrl is wanted and supported.
  let enabled = false;
  const apply = (): void => {
    const wantsCtrl = bindings.getState().modifier !== 'alt';
    const shouldEnable = wantsCtrl && supported;
    if (shouldEnable && !enabled) {
      driver.enable();
      shim.setBypass(false);
      enabled = true;
    } else if (!shouldEnable && enabled) {
      driver.disable();
      shim.setBypass(true);
      enabled = false;
    }
  };
  apply();
  const unsubscribe = bindings.subscribe(apply);

  // Best-effort pop on abnormal exit so the parent shell's input isn't left in protocol or mouse mode
  // (a terminal stuck in mouse reporting spews escape codes on every move/click).
  const popOnExit = (): void => {
    if (enabled) {
      driver.disable();
      enabled = false;
    }
    if (mouseEnabled) {
      process.stdout.write(MOUSE_OFF);
      shim.setMouseEnabled(false);
      mouseEnabled = false;
    }
  };
  process.on('exit', popOnExit);
  process.on('SIGTERM', popOnExit);

  return () => {
    unsubscribe();
    process.off('exit', popOnExit);
    process.off('SIGTERM', popOnExit);
    popOnExit();
  };
}

/**
 * Clear Ink's terminal surface on every real terminal-size change.
 *
 * Ink's built-in resize handler clears only when the width decreases. That protects the common
 * narrow-resize case, but monitor moves / font scaling can change rows or expand dimensions while
 * leaving stale cells in the alternate screen. A pane toggle fixes the symptom because it causes a
 * later full-enough repaint; repainting here makes the resize itself the repaint boundary while
 * preserving incremental rendering for normal typing.
 */
export function installResizeClear(
  stdout: {
    columns: number;
    rows: number;
    on(event: 'resize', listener: () => void): unknown;
    off(event: 'resize', listener: () => void): unknown;
  },
  clear: () => void,
): () => void {
  let previousColumns = stdout.columns;
  let previousRows = stdout.rows;
  let timeout: NodeJS.Timeout | undefined;
  const onResize = (): void => {
    const nextColumns = stdout.columns;
    const nextRows = stdout.rows;
    if (nextColumns === previousColumns && nextRows === previousRows) {
      return;
    }
    previousColumns = nextColumns;
    previousRows = nextRows;
    if (timeout !== undefined) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(() => {
      timeout = undefined;
      clear();
    }, 75);
  };
  stdout.on('resize', onResize);
  return () => {
    if (timeout !== undefined) {
      clearTimeout(timeout);
      timeout = undefined;
    }
    stdout.off('resize', onResize);
  };
}

/** Detect kitty support only on a real interactive TTY; a non-TTY stdin (piped/CI) can't carry the
 * protocol, so we skip the probe and report unsupported without writing a query to a non-terminal. */
function detectIfTty(shim: StdinShim, driver: KeyProtocolDriver): Promise<boolean> {
  if (shim.isTTY !== true || process.stdout.isTTY !== true) {
    return Promise.resolve(false);
  }
  return driver.detect();
}

/** Register a (re)connect listener if the client exposes `onConnect` (the live {@link UdsBusClient}
 * does; the fake does not). Narrowed structurally so the seam stays the transport-agnostic
 * {@link BusClient}, exactly as {@link closeIfSupported} does. Returns the disposer, or `undefined`
 * when unsupported so the caller can fall back to a one-shot prime. */
function onConnectIfSupported(bus: BusClient, listener: () => void): (() => void) | undefined {
  const maybe = bus as BusClient & { onConnect?: (listener: () => void) => () => void };
  return maybe.onConnect?.(listener);
}

/** Register a disconnect listener if the client exposes `onDisconnect` (the live
 * {@link UdsBusClient} does; the fake does not). Same structural narrowing as
 * {@link onConnectIfSupported}; returns the disposer, or `undefined` when unsupported. */
function onDisconnectIfSupported(bus: BusClient, listener: () => void): (() => void) | undefined {
  const maybe = bus as BusClient & { onDisconnect?: (listener: () => void) => () => void };
  return maybe.onDisconnect?.(listener);
}

/** Register a permanent-error listener if the client exposes `onPermanentError` (the live
 * {@link UdsBusClient} does; the fake does not). Same structural narrowing as
 * {@link onConnectIfSupported}; returns the disposer, or `undefined` when unsupported. */
function onPermanentErrorIfSupported(
  bus: BusClient,
  listener: (error: Error) => void,
): (() => void) | undefined {
  const maybe = bus as BusClient & {
    onPermanentError?: (listener: (error: Error) => void) => () => void;
  };
  return maybe.onPermanentError?.(listener);
}

/** Close the bus connection if the client exposes a `close()` (the live {@link UdsBusClient} does;
 * the fake does not). Narrowed structurally so the seam stays the transport-agnostic `BusClient`. */
function closeIfSupported(bus: BusClient): void {
  const maybe = bus as BusClient & { close?: () => void };
  maybe.close?.();
}

/** Construct the live bus client from the env-provided socket path. */
function makeLiveBus(): BusClient {
  return new UdsBusClient({ socketPath: resolveSocketPath() });
}

/**
 * One-shot smoke mount: render against a {@link FakeBusClient} seeded with canned data, paint once,
 * then unmount so the run terminates instead of blocking. No socket, no service. This is what F8's
 * build gate calls to prove the bundle loads.
 */
export async function runSmoke(): Promise<void> {
  const bus = makeSmokeBus();
  const { store, dispose } = createAppStore(bus);
  const inputStores = createInputStores(STARTUP_PANELS);
  void store.getState().actions.roster.refresh();
  void store.getState().actions.usage.refresh();

  const instance = render(<App store={store} inputStores={inputStores} bus={bus} />);
  setImmediate(() => {
    instance.unmount();
  });
  await instance.waitUntilExit();
  dispose();
}

/** A {@link FakeBusClient} seeded with one crow + idle usage so the smoke frame paints both regions. */
function makeSmokeBus(): FakeBusClient {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', {
    invalidation_key: 'smoke',
    sessions: [
      {
        agent_id: 'collaborator',
        role: 'collaborator',
        status: 'idle',
        harness: 'claude',
        model: 'anthropic/claude-opus',
        session_name: 'collaborator',
      },
    ],
  });
  fake.stubRpc('state.schedule_snapshot', {
    invalidation_key: 'smoke',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
  });
  return fake;
}

/** Dispatch on argv: `--smoke` → one-shot smoke; otherwise the standing live runner. */
export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<void> {
  if (argv.includes('--smoke')) {
    await runSmoke();
    return;
  }
  await runLive();
}

/** True when this module is the process entrypoint (so importing it in a test does not run it). */
function isEntrypoint(): boolean {
  const entry = process.argv[1];
  if (entry === undefined) {
    return false;
  }
  return fileURLToPath(import.meta.url) === entry;
}

if (isEntrypoint()) {
  main().catch((error: unknown) => {
    process.exitCode = 1;
    console.error(error);
  });
}
