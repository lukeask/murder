# Application client

`ApplicationClient` is the sole injected client seam for Ink. `ApplicationWebSocketClient`
speaks the generated, closed application WebSocket protocol; `FakeApplicationClient` is its
in-memory test double. There is no fallback Unix-socket or generic message transport here.
