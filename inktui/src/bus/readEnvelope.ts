/**
 * The read-RPC envelope contract, in one place so the real and fake bus clients cannot drift.
 *
 * The service wraps every `state.*` read handler's DTO as `{ ok: true, value: <dto> }` (the
 * `_value()` helper in `murder/app/service/host.py`), a shape the still-live Textual client depends
 * on (`_request_value`/`_request_optional` read `reply["value"]`). The Ink store, by contrast,
 * reads read-RPC fields at TOP LEVEL (`reply.sessions`, `reply.body`, projections in `listSlice.ts`),
 * matching the bare DTO. So the envelope is unwrapped at the single transport seam, gated on the
 * `state.` prefix: every wrapped handler is `state.*`, and writes/commands (`command.*`, `ticket.*`,
 * `agent.*`, `image.*`) already return `{ ok, ...fields }` top-level and must NOT be unwrapped.
 *
 * Both {@link UdsBusClient} (which receives the wrapped reply off the wire) and
 * {@link FakeBusClient} (which wraps a stub's DTO to model the live shape) call {@link unwrapReadReply}
 * so they apply identical gating and identical null-not-found semantics.
 */

/**
 * Recognize the service's read-RPC envelope `{ ok: true, value: <dto> }`. A reply that lacks a
 * `value` key (a write/command result, which is `{ ok, ...fields }`) is left untouched.
 */
export function isReadEnvelope(reply: Record<string, unknown>): reply is { value: unknown } {
  return 'value' in reply;
}

/**
 * Apply the `state.`-gated read-RPC unwrap to a settled RPC reply, returning the bare DTO the store
 * reads top-level.
 *
 * For a `state.*` method whose reply is a `{ value }` envelope, return `.value` verbatim — including
 * `null`, which `_state_ticket_detail` (and the `*_display` reads) emit for not-found via
 * `_value(None)`. That `null` is the not-found signal the store's detail/doc-view paths key on;
 * coercing it (e.g. `?? {}`) would resurrect the fake-vs-live divergence this gating exists to close.
 * Every other method (and any non-enveloped reply) is returned untouched.
 */
export function unwrapReadReply(method: string, reply: Record<string, unknown>): unknown {
  if (method.startsWith('state.') && isReadEnvelope(reply)) {
    return reply.value;
  }
  return reply;
}
