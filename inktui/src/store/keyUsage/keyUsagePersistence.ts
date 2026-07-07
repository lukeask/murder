/**
 * `keyUsagePersistence` — disk-backed key-usage counts under XDG state (TS-owned mutable state).
 *
 * The Python service owns config under `XDG_CONFIG_HOME`; this file lives under
 * `XDG_STATE_HOME/murder/key_usage.json` because it is written by the Ink runner as the user
 * dispatches bindings. {@link loadKeyUsage} is defensive (never throws); {@link startKeyUsagePersistence}
 * hydrates on startup, debounces async writes, and flushes synchronously on teardown.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import {
  keyUsageStore,
  type KeyUsageRecord,
  type KeyUsageStoreApi,
} from './keyUsageStore.js';

const MAX_PERSISTED_ENTRIES = 200;
const WRITE_DEBOUNCE_MS = 5000;

/** Resolve the on-disk path for key-usage counts (`$XDG_STATE_HOME/murder/key_usage.json`). */
export function keyUsagePath(): string {
  const base = process.env['XDG_STATE_HOME'] ?? path.join(homedir(), '.local', 'state');
  return path.join(base, 'murder', 'key_usage.json');
}

function isValidRecord(value: unknown): value is KeyUsageRecord {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record['count'] === 'number' &&
    Number.isFinite(record['count']) &&
    typeof record['lastAt'] === 'number' &&
    Number.isFinite(record['lastAt'])
  );
}

/** Cap a record map to the `limit` entries with the highest `lastAt` (ties arbitrary). */
function capByRecency(
  actions: Record<string, KeyUsageRecord>,
  limit: number,
): Record<string, KeyUsageRecord> {
  const entries = Object.entries(actions);
  if (entries.length <= limit) {
    return actions;
  }
  entries.sort((a, b) => b[1].lastAt - a[1].lastAt);
  return Object.fromEntries(entries.slice(0, limit));
}

/** Parse and validate persisted key-usage data. Missing file, corrupt JSON, or bad shape → `{}`. */
export function loadKeyUsage(filePath: string = keyUsagePath()): Record<string, KeyUsageRecord> {
  try {
    const raw = readFileSync(filePath, 'utf8');
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    const actions: Record<string, KeyUsageRecord> = {};
    for (const [actionId, value] of Object.entries(parsed)) {
      if (isValidRecord(value)) {
        actions[actionId] = { count: value.count, lastAt: value.lastAt };
      }
    }
    return capByRecency(actions, MAX_PERSISTED_ENTRIES);
  } catch {
    return {};
  }
}

function writeActionsSync(filePath: string, actions: Record<string, KeyUsageRecord>): void {
  const dir = path.dirname(filePath);
  mkdirSync(dir, { recursive: true });
  writeFileSync(filePath, `${JSON.stringify(actions, null, 2)}\n`, 'utf8');
}

async function writeActionsAsync(
  filePath: string,
  actions: Record<string, KeyUsageRecord>,
): Promise<void> {
  const dir = path.dirname(filePath);
  await mkdir(dir, { recursive: true });
  await writeFile(filePath, `${JSON.stringify(actions, null, 2)}\n`, 'utf8');
}

/**
 * Hydrate `store` from disk, then persist changes (debounced async writes; sync flush on cleanup).
 * Returns a teardown that unsubscribes, cancels the pending timer, and writes once if dirty.
 */
export function startKeyUsagePersistence(
  store: KeyUsageStoreApi = keyUsageStore,
  filePath: string = keyUsagePath(),
): () => void {
  store.getState().hydrate(loadKeyUsage(filePath));

  let dirty = false;
  let timer: NodeJS.Timeout | undefined;

  const flushSync = (): void => {
    try {
      writeActionsSync(filePath, store.getState().actions);
      dirty = false;
    } catch {
      // Best-effort telemetry — never crash the TUI on a write failure.
    }
  };

  const flushAsync = async (): Promise<void> => {
    try {
      await writeActionsAsync(filePath, store.getState().actions);
      dirty = false;
    } catch {
      // Best-effort telemetry — never crash the TUI on a write failure.
    }
  };

  const scheduleWrite = (): void => {
    dirty = true;
    if (timer !== undefined) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = undefined;
      void flushAsync();
    }, WRITE_DEBOUNCE_MS);
  };

  const unsubscribe = store.subscribe(scheduleWrite);

  return () => {
    unsubscribe();
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
    if (dirty) {
      flushSync();
    }
  };
}
