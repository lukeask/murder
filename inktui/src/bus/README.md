# src/bus

The Ink TUI's transport seam for Murder's service-owned application protocol.

- `BusClient.ts` exposes only product-level `query(name, params)`,
  `command(name, params)`, projection `hydrate(topics, listener)`, and independent
  `attachTerminal(sessionId, listener)` operations. Feature action modules declaration-merge their
  DTOs into `QueryMethods` and `CommandMethods`; names are constrained by the generated contract.
- `UdsBusClient.ts` carries generated application messages over one persistent JSON-lines Unix
  socket. It performs `client.hello` / `server.hello`, correlates requests, resumes projection and
  error-notification subscriptions by cursor, and reattaches terminal streams after reconnect.
  Hydration and terminal disposers send real `unsubscribe` and `terminal.detach` messages.
- `FakeBusClient.ts` mirrors the same seam with `stubQuery`, `stubCommand`, projection emission, and
  terminal-frame emission for tests.
- `../generated/applicationProtocol.ts` is generated from the backend Pydantic application
  contracts. Client code supplies those public query/command names directly; mapping to transitional
  internal handlers exists only in the backend gateway.
- `protocol.ts` remains temporarily as compatibility DTO types for projection payloads. It is not
  the public wire protocol and its legacy `rpc`, `sub`, `hydrate`, or `tmux.frame` envelopes are
  never sent by these clients.
