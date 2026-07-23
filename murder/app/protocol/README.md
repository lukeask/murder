# Murder application protocol

This package is the authoritative client contract. Interactive clients begin
with `client.hello` and may then use only the closed query, command,
subscription, and terminal-stream capabilities advertised by `server.hello`.

The service gateway validates those capabilities and invokes enum-keyed,
in-process application services directly. Request/reply dispatch does not
publish a command event or enter the broker. Clients cannot select a legacy
RPC handler, worker address, event type, or event filter, and cannot publish
internal events. Projection subscriptions own
durable cursors; terminal streams have independent identities and monotonic
replace-frame sequences.

High-risk capabilities validate params and results at the gateway against
typed models in `sessions.py`, `permissions.py`, and `workflows.py` (writer
leases, session commands, approvals, permission grants, workflow
definition/start, run inspection, and external signaling). The gateway accepts
only protocol request models at the transport boundary; dictionary payloads
are validated into the capability-specific contracts.
Wire `request_id` / `subscription_id` / `stream_id` remain opaque transport
strings; domain correlation uses UUIDs, bridged once via
`murder.contracts.common.domain_request_id`.

The `facts` subscription is the sole retained-outcome surface. It replays only
feature-owned immutable fact envelopes from `retained_facts`; it never exposes
the generalized compatibility `events` table. Projection-input cursors are
written transactionally with their source facts, while workflow signals,
terminal bytes, session commands, and immediate queries/decisions remain in
their owning non-fact paths.

Run `python tools/generate_application_protocol.py` after changing a Pydantic
contract. CI verifies that
`inktui/src/generated/applicationProtocol.ts` matches the Python schema.

Read-reply DTOs live in `read_models.py`. There is no parallel service-client
protocol: websocket request, subscription, and terminal messages in this
package are the complete public client surface.
