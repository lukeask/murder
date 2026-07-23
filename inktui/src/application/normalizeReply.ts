/**
 * Transitional backend read handlers still return `{ok:true,value:<dto>}` through the application
 * gateway. Public query names no longer expose the legacy `state.*` prefix, so recognition is based
 * solely on the envelope shape at this one compatibility seam.
 */

export function isReadEnvelope(reply: unknown): reply is { ok: true; value: unknown } {
  return (
    typeof reply === 'object' &&
    reply !== null &&
    !Array.isArray(reply) &&
    (reply as Record<string, unknown>)['ok'] === true &&
    'value' in reply
  );
}

/** Return the public DTO value while leaving ordinary command/query result objects untouched. */
export function unwrapReadReply(_name: string, reply: unknown): unknown {
  return isReadEnvelope(reply) ? reply.value : reply;
}
