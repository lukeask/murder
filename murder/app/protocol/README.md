# Murder application protocol

This package is the authoritative client contract. Interactive clients begin
with `client.hello` and may then use only the closed query, command,
subscription, and terminal-stream capabilities advertised by `server.hello`.

The service gateway maps those capabilities to the transitional internal bus.
Clients cannot select an RPC handler, worker address, event type, or event
filter, and cannot publish internal events. Projection subscriptions own
durable cursors; terminal streams have independent identities and monotonic
replace-frame sequences.

The `facts` subscription is the sole retained-outcome surface. It replays only
feature-owned immutable fact envelopes from `retained_facts`; it never exposes
the generalized compatibility `events` table. Projection-input cursors are
written transactionally with their source facts, while workflow signals,
terminal bytes, session commands, and immediate queries/decisions remain in
their owning non-fact paths.

Run `python tools/generate_application_protocol.py` after changing a Pydantic
contract. CI verifies that
`inktui/src/generated/applicationProtocol.ts` matches the Python schema.
