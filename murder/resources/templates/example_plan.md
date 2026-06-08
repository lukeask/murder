---
name: example-plan
status: draft
---

# Example plan — copy me to plans/<name>.md

This is a copyable example plan. To make a real plan, copy this file to
`.murder/plans/<name>.md` and edit it. This example lives at the `.murder/`
top level so the sync worker never ingests it — it will not appear in the TUI.

Frontmatter fields:
- `name`: the plan's stable identifier (matches the filename).
- `status`: one of `draft`, `accepted`, `superseded`.
- `parent`: an optional parent plan name (omit for a top-level plan).

Use the body to describe the goal, the approach, and the work breakdown. When a
plan carves tickets, each ticket gets its own `.murder/tickets/<id>.md` (see
`example_ticket.md`).

# Checklist
Edit each item to `[x]` incrementally, the moment you finish it — not all at the
end, so progress is visible as the plan advances.
[ ] First milestone
[ ] Second milestone
[ ] Final milestone — review and accept the plan
