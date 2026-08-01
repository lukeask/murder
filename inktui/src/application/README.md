# TUI application factory

The renderer-neutral `ApplicationClient`, `ApplicationWebSocketClient`, and
`FakeApplicationClient` live in `ui-core/src/application/`. This directory owns only TUI-specific
construction defaults: URL/configuration, client-ID creation, Node-compatible WebSocket injection,
and TUI logging. There is no fallback Unix-socket or generic message transport here.
