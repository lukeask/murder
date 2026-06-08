#!/usr/bin/env node
import { render } from 'ink';
import { App } from './components/App.js';

/**
 * Process entrypoint. Renders the Ink tree and resolves when it unmounts. For the C0 scaffold
 * the app paints once and exits clean (no input loop yet) so `npm run dev` is a non-hanging
 * smoke test of the toolchain. C4 introduces the root `useInput` dispatcher that keeps it
 * alive; at that point this stays the thin entrypoint and the lifecycle lives in the app shell.
 */
async function main(): Promise<void> {
  const instance = render(<App />);
  // Scaffold-only: unmount on the next tick so the smoke test terminates instead of blocking
  // the terminal. Remove once C4 owns the input lifecycle.
  setImmediate(() => {
    instance.unmount();
  });
  await instance.waitUntilExit();
}

main().catch((error: unknown) => {
  process.exitCode = 1;
  console.error(error);
});
