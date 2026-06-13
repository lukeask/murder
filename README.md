# murder

Multi-agent AI orchestrator — run Claude Code, Codex, and Cursor crows side-by-side from a single terminal.

## Requirements

- Python 3.12+
- Node.js 20+
- tmux

## Install

```shell
pip install murder
```

## Quickstart

1. `murder init` — scaffold `.murder/` and `roles.yaml` in your project
2. Set at least one API key (e.g. `ANTHROPIC_API_KEY=...`)
3. `murder` — launch the TUI

Run `murder doctor` to check all prerequisites.

## Harnesses

murder supports three first-class AI coding harnesses: Claude Code, Codex, and Cursor. See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for current limitations and [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## Keys (TUI)

- `?` — help overlay
- `alt+s` — spawn a crow
- `ctrl+1`–`ctrl+5` — switch panels (plans/crows/schedule/tickets/history)
- `:help` — list commands
- `/…` — pass command to active harness
