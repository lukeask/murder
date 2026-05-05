# murder

Agentic dev harness. A *murder* of crows supervises a *monkey*.

> Status: pre-M0 scaffold. Nothing runs yet. See `.agents/` for the design
> docs that drive this codebase:
> - `initialbrainstorm.md` — philosophy & flow
> - `1777410436NOTES.md` — settled naming, role hierarchy, event flow
> - `harnesses_spec.md` — adapter dir requirements
> - `furtherspecproposal.md` — v0 spec; **§v0 Final Direction is the
>   build target.** Earlier sections are the brainstorm trail.

## The cast

| Role | What it is | Lives in |
|---|---|---|
| **Collaborator** | Planning chat partner. Wraps Claude Code. | `agents/collaborator.py` |
| **Sentinel** | Tech-lead overseer. Codebase-aware. One global. | `agents/sentinel.py` |
| **Augur** | Per-Monkey driver. Cheap + programmatic. | `agents/augur.py` |
| **Monkey** | Implementer. Wraps cursor / claude-code / pi. | `agents/monkey.py` |

## Quick start (when this works)

```bash
pip install -e .      # editable install of the murder package
murder init           # creates .agents/ + .agents/murder.db
murder doctor         # checks tmux, OPENROUTER_API_KEY, harness binaries
murder                # bare command: launches the TUI (alias: `murder up`)
# in the chat pane: `/murder` kicks off all ready tickets;
# anything else routes to the Collaborator (Claude Code, lazy-spawned).
murder ticket create t001 "Dogfood the CLI" --wave 0 --plan "Implement the first path."
murder ticket create t002 "Import markdown" --from ./ticket.md --check "Run tests"
murder kick t007      # one-shot: kick off just t007's Monkey, no TUI
```

## Layout

```
murder/                # the package
├── cli.py             # `murder` entrypoint
├── config.py          # roles.yaml + global/.agents/root .env loading
├── db.py              # SQLite schema + access (D2)
├── bus.py             # typed AgentEvent union + asyncio pubsub (D4)
├── tmux.py            # session helpers, load-buffer for big sends (D10)
├── runtime.py         # async runtime + supervisor + flock
├── orchestrator.py    # spawn/kill agents; wave kickoff; ready computation
├── escalations.py     # escalation queue helpers
├── harnesses/         # interactive-CLI wrappers (cursor, cc, pi, native)
├── clients/           # native LLM clients (OpenRouter)
├── agents/            # Collaborator, Sentinel, Augur, Monkey
├── tickets/           # schema, parser, waves, lifecycle, checklist protocol
├── plans/             # plan markdown schema/parser
├── enforcement/       # write-set live + post-hoc enforcement (D5)
├── tui/               # Textual app
├── storage/           # paths, runs, filesystem
├── prompts/           # role prompt templates
└── templates/         # files copied into a project's .agents/ on `murder init`
```

`murder init` creates `.agents/.env` from the bundled example. Environment
loading order is `~/.config/murder/.env`, then `.agents/.env`, then repo-root
`.env`, with later files overriding earlier values.

## See also

`.agents/initialbrainstorm.md` for the why.
