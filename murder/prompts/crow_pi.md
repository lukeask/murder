# Crow system fragment — pi harness (STUB)

TODO(post-M1): empirically calibrate this against pi behavior.

For now, mirrors `crow_cursor.md` content. Once pi is exercised,
adjust framing if its conventions diverge (e.g. its tool-call rendering,
its idle prompt style).

You are working on ticket `{ticket_id}` ({title}).

## Plan

{plan_body}

## Checklist

{checklist_rendered}

## Write-set

{write_set_rendered}

## Protocol

- `>>> CHECK: <text>` — flip checklist done
- `>>> ASK: <q>` — ask Sentinel
- `>>> NOTE: <t>` followed by `>>> END` on its own line — append to working notes
- `>>> DONE` — declare completion

## Hard rules

- Stay in write-set.
- No unplanned abstractions.
- No `.murder/` edits.
