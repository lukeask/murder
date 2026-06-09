/**
 * Shared `command.submit` helper — the one place the orchestrator command-bus write protocol lives.
 *
 * Several Ink write actions (`ticket.quick_create`, `crow.spawn_rogue`, and the chat
 * `agent.message`) are NOT standalone RPCs on the live bus: they are orchestrator *command kinds*
 * dispatched through the live `command.submit` / `command.status` pair (registered in
 * `murder/app/service/host.py`; the orchestrator worker dispatches on `command.kind` —
 * `murder/runtime/workers/orchestrator_worker.py`). This helper encapsulates the submit-then-poll
 * protocol the Textual client uses (`murder/app/tui/client.py`) so each caller supplies only the
 * `kind` + `payload` and receives the parsed terminal result.
 *
 * Protocol (mirrors the live handler):
 *  1. `command.submit { target_worker, kind, payload }` → `{ command_id }`.
 *  2. Poll `command.status { command_id }` until `status` is `'done'` (resolve with the parsed
 *     `result_json`) or `'failed'` (reject with `last_error`).
 *
 * All current callers target `target_worker: 'orchestrator'`.
 */

import type { BusClient, RpcPayload } from '../bus/BusClient.js';

/** Default worker for orchestrator command kinds. */
export const ORCHESTRATOR_WORKER = 'orchestrator';

/** Poll interval (ms) between `command.status` checks. Mirrors the Textual client's cadence. */
const POLL_INTERVAL_MS = 100;

/** Max number of status polls before giving up (keeps a failed/stuck command from hanging forever). */
const MAX_POLLS = 600; // ~60s at 100ms — generous for spawn (the slowest command).

/** Resolve after `ms` milliseconds. Extracted so tests can stub timing if needed. */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Submit an orchestrator command and await its terminal result.
 *
 * @returns the parsed `result_json` (the worker's reply payload), or `{}` when the worker returned
 *   no body. Rejects with the worker's `last_error` on a failed command, or a timeout error if the
 *   command never reaches a terminal state.
 */
export async function submitCommand(
  bus: BusClient,
  kind: string,
  payload: RpcPayload,
  options: { targetWorker?: string } = {},
): Promise<RpcPayload> {
  const submitted = await bus.rpc('command.submit', {
    target_worker: options.targetWorker ?? ORCHESTRATOR_WORKER,
    kind,
    payload,
  });
  const commandId = submitted.command_id;
  if (!commandId) {
    throw new Error(`${kind}: command.submit returned no command_id`);
  }

  for (let i = 0; i < MAX_POLLS; i++) {
    const status = await bus.rpc('command.status', { command_id: commandId });
    if (status.status === 'done') {
      const raw = status.result_json;
      return raw != null && raw !== '' ? (JSON.parse(raw) as RpcPayload) : {};
    }
    if (status.status === 'failed') {
      throw new Error(status.last_error ?? `${kind} failed`);
    }
    await delay(POLL_INTERVAL_MS);
  }
  throw new Error(`${kind} timed out`);
}
