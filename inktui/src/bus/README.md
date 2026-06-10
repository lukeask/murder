# src/bus

The transport seam. `BusClient` interface, `FakeBusClient` test double, the real
`UdsBusClient` (Unix-socket JSON-RPC, C2), and the wire `protocol.ts`. This is the **only** layer
that knows about sockets/JSON-RPC. Injected into the store; nothing above this layer imports it
directly. (Rule 4.)

- `protocol.ts` — types + constants only (no sockets/Ink), ported 1:1 from
  `murder/bus/protocol.py`. Discriminated unions: `WireMessage` (by `op`), `BusEvent` (by
  `type`). `PROTOCOL_VERSION` is kept in lockstep with the Python source.
- `BusClient.ts` — the injected interface: typed `rpc(method, params)` (via the `RpcMethods`
  registry) + `subscribe(listener, filter?)` returning an `Unsubscribe`. Events are delivered to a
  **callback** (the Zustand/`useSyncExternalStore` observer shape), not an async iterator.
- `FakeBusClient.ts` — in-memory double: `emit(event)` pushes to matching subscribers
  synchronously; `stubRpc(method, reply|handler)` cans replies; `rpcCalls` / `subscriberCount`
  expose state for assertions. The double the whole store layer is tested against.
- `UdsBusClient.ts` — the real client (C2). A **single persistent** Unix-socket connection,
  multiplexed: JSON-lines framing (`LineBuffer` reassembles partial reads), Hello/Ack handshake
  with `PROTOCOL_VERSION` refusal, exponential-backoff-with-full-jitter reconnect, and one stated
  error policy (RPC rejects on timeout/err/drop; subscriptions auto-re-establish on reconnect so
  the store never re-subscribes). All deps (`socketPath`, `clock`, `backoff`, `logger`) are
  injected (rule 4). Read the module docstring for the framing/connection/error policy in full.
