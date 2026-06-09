#!/usr/bin/env node
import { fileURLToPath } from 'node:url';
import { render } from 'ink';
import type { BusClient } from './bus/BusClient.js';
import { FakeBusClient } from './bus/FakeBusClient.js';
import { UdsBusClient } from './bus/UdsBusClient.js';
import { App } from './components/App.js';
import { createInputStores } from './input/createInputStores.js';
import type { PanelId } from './input/panels.js';
import { createAppStore } from './store/store.js';

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

/** The panels seeded visible on startup so the shell paints both regions with live data. */
const STARTUP_PANELS: readonly PanelId[] = ['plans', 'usage', 'crows'];

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
 * Mount the shell against a live {@link UdsBusClient} and hold the terminal open until the user
 * exits. The store opens its bus subscriptions on construction; we additionally prime the visible
 * slices so the first paint shows live data on a quiescent service too (subscribe replay only
 * carries already-persisted events, and a fresh slice would otherwise sit empty until the next
 * server-side change — so we pull once, exactly as the smoke path did). Returns when the app exits.
 */
export async function runLive(busFactory: () => BusClient = makeLiveBus): Promise<void> {
  const bus = busFactory();
  const { store, dispose } = createAppStore(bus);
  const inputStores = createInputStores(STARTUP_PANELS);

  // Prime the visible slices so the shell paints live data immediately. Fire-and-forget: the actions
  // route their own errors into each slice's `error` field, and the subscription keeps the slices
  // live thereafter via key-only invalidation.
  void store.getState().actions.roster.refresh();
  void store.getState().actions.usage.refresh();
  void store.getState().actions.plans.refresh();

  const instance = render(<App store={store} inputStores={inputStores} bus={bus} />);
  // No unmount-on-tick here (that was the smoke scaffold): the app stays mounted. Ink keeps the
  // process alive and resolves `waitUntilExit` when the user exits (ctrl+c by default).
  try {
    await instance.waitUntilExit();
  } finally {
    dispose();
    closeIfSupported(bus);
  }
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
