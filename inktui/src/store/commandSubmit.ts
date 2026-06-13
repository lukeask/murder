/**
 * Shared `command.submit` helper — the one place the orchestrator command-bus write protocol lives.
 *
 * Several Ink write actions (`ticket.quick_create`, `crow.spawn_rogue`, and the chat
 * `agent.message`) are NOT standalone RPCs on the live bus: they are orchestrator *command kinds*
 * dispatched through the live `command.submit` / `command.status` pair (registered in
 * `murder/app/service/host.py`; the orchestrator worker dispatches on `command.kind` —
 * `murder/runtime/workers/orchestrator_worker.py`). This helper encapsulates the submit-then-poll
 * protocol defined in the service host so each caller supplies only the
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

/**
 * Poll interval (ms) between `command.status` checks. The poll exists because the live bus exposes
 * command lifecycle ONLY through the `command.submit` + `command.status` request/response pair — the
 * service does not (yet) push command-completion/terminal events. So this loop mirrors the SERVER's
 * contract, not any legacy client; it is a deliberate, timeout-bounded choke point confined to this
 * one helper.
 *
 * Follow-up: once the Python service pushes command-terminal events over the bus, this poll loop can
 * be deleted in favour of awaiting that event.
 */
const POLL_INTERVAL_MS = 100;

/** Max number of status polls before giving up (keeps a failed/stuck command from hanging forever). */
const MAX_POLLS = 600; // ~60s at 100ms — generous for spawn (the slowest command).

/**
 * Max consecutive `command.status` poll rejections to tolerate before giving up. A mid-command
 * socket drop rejects the in-flight `command.status` RPC (the UdsBusClient fails all pending RPCs on
 * disconnect), but the command is still running server-side under the SAME `command_id` — and the
 * UdsBusClient auto-reconnects. So a transient blip should NOT orphan the command: we re-poll the
 * same `command_id` until the connection comes back (resume-by-command_id), bounded so a truly dead
 * connection still terminates instead of looping forever. A successful poll resets the counter.
 */
const MAX_POLL_RETRIES = 50; // ~5s of reconnect grace at 100ms between retries.

/** Resolve after `ms` milliseconds. Extracted so tests can stub timing if needed. */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Whether a `command.status` rejection is a permanent give-up (the client itself was closed/shut
 * down) versus a transient drop the auto-reconnect will recover from. We detect the permanent case
 * by message so this helper stays transport-agnostic (no import of the concrete error class).
 */
function isClientClosed(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes('client is closed') || message.includes('client closed');
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

  let retries = 0;
  for (let i = 0; i < MAX_POLLS; i++) {
    let status: import('../bus/BusClient.js').CommandStatusResult;
    try {
      status = await bus.rpc('command.status', { command_id: commandId });
    } catch (error: unknown) {
      // Resume-by-command_id: a mid-command socket drop rejects this poll, but the command keeps
      // running server-side and the UdsBusClient auto-reconnects. Re-poll the SAME `command_id`
      // through the blip rather than reporting a phantom failure for a command that may still
      // complete. Give up only if the client is permanently closed or we exhaust the retry grace.
      if (isClientClosed(error) || ++retries > MAX_POLL_RETRIES) {
        throw error;
      }
      await delay(POLL_INTERVAL_MS);
      continue;
    }
    retries = 0; // a successful poll clears the transient-failure budget.
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
