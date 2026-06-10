---
title: Example ticket — copy me to tickets/<id>.md
deps: []
harness: claude_code
model: claude-opus-4-8
worktree: null
---

This is a copyable example ticket. To make a real ticket, copy this file to
`.murder/tickets/<id>.md` (the id must contain a digit, e.g. `t007`), then edit
the frontmatter and the checklist below. This example lives at the `.murder/`
top level so the sync worker never ingests it — it will not appear in the TUI.

Frontmatter fields (agent-authored only; runtime state lives in the DB):
- `title`: short human-readable summary.
- `deps`: list of ticket ids this ticket waits on (e.g. `[t001, t002]`).
- `harness`: which agent harness runs this ticket (see HARNESSES_AND_MODELS.md).
- `model`: the model id for that harness.
- `worktree`: an isolated worktree name, or `null` to run in the main repo.

# Checklist
Edit each item to `[x]` incrementally, the moment you finish it — not all at the
end. The sync worker reads this list into the DB on every save, so toggling a box
is how progress is reported.
[ ] First step of the work
[ ] Second step of the work
[ ] Final step — run the tests and leave the tree green
