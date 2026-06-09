#!/usr/bin/env node
import { render } from 'ink';
import { FakeBusClient } from './bus/FakeBusClient.js';
import { App } from './components/App.js';
import { createInputStores } from './input/createInputStores.js';
import { createAppStore } from './store/store.js';

/**
 * Process entrypoint — constructs the injected stores and renders the real app shell (rule 4: the
 * bus is wired in exactly here, never imported by a component).
 *
 * Backbone note: C5 builds against the {@link FakeBusClient} (the live `UdsBusClient` lands with the
 * service per the plan's "backbone first" section), so `npm run dev` renders the real shell — top
 * bar, the reference crows panel, chat input, bottom bar — driven by a fake bus seeded with one
 * crow. Swapping in `UdsBusClient` here is a one-line change when the socket is live; nothing above
 * this file knows which client it is. The smoke loop paints once and exits clean so `npm run dev`
 * doesn't block the terminal — the standing input loop arrives with the live runner.
 */
function makeDevBus(): FakeBusClient {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', {
    invalidation_key: 'dev',
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
  // C9: usage is embedded in the schedule snapshot; stub it so UsagePanel renders idle on startup.
  fake.stubRpc('state.schedule_snapshot', {
    invalidation_key: 'dev',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
  });
  return fake;
}

async function main(): Promise<void> {
  const bus = makeDevBus();
  const { store, dispose } = createAppStore(bus);
  // Seed a couple of panels on so the shell paints both regions for the smoke test (the crows
  // reference panel on the right, a placeholder on the left).
  const inputStores = createInputStores(['plans', 'usage', 'crows']);
  // Prime the panels with the fake bus's canned data (the live app pulls on the first
  // `state.snapshot`; the smoke test has no live events, so kick one refresh each).
  void store.getState().actions.roster.refresh();
  void store.getState().actions.usage.refresh();

  const instance = render(<App store={store} inputStores={inputStores} bus={bus} />);
  // Smoke-only: unmount on the next tick so the dev run terminates instead of blocking. The standing
  // input loop (which keeps the app alive) lands with the live runner; here we just prove it paints.
  setImmediate(() => {
    instance.unmount();
  });
  await instance.waitUntilExit();
  dispose();
}

main().catch((error: unknown) => {
  process.exitCode = 1;
  console.error(error);
});
