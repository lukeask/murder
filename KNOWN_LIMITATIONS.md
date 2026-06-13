# Known limitations — v0

These are accepted ship-as-is limitations for the v0 release.

## Grammar version pinning

Parsing grammars for Claude Code, Codex, and Cursor are pinned to specific UI versions. If a harness updates its UI (glyphs, verb list, ANSI colors), the grammar may mis-parse output. The raw tmux mirror (`ctrl+y`) is the designed last-resort fallback.

## Session continuity

Planner sessions restart fresh after a murder restart (until `/resume`-based resumability ships). Crow sessions can be reattached via the history panel (`r` keybind on a resumable row).

## Disconnect replay

`conversation.block` events during a TUI disconnect are not replayed. The snapshot reprime on reconnect covers state fields but individual block content may be missed.

## Terminal

`ctrl+m` chord requires kitty keyboard protocol. Standard xterm terminals cannot bind ctrl+m separately from Enter.

## Platform

No Windows support. Linux + macOS only.

## Web/phone clients

`stream.unsubscribe` RPC is deferred until a web or phone client lands.
