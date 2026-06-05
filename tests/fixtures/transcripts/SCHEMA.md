# Parsed transcript ground-truth schema (parser v2)

Ground-truth fixtures for the **new** harness transcript parsing stack (the rewrite that
replaces `murder/llm/harnesses/transcripts.py`'s flat `(role, text)` model). Each scenario dir
holds an input frame sequence and the expected parsed document:

```
transcripts/<harness>/
  frames/0000.txt … NNNN.txt   # consecutive raw pane captures (keyframes; verbatim, ANSI-stripped)
  expected.json                # the accumulated TranscriptDoc after feeding every frame in order
```

The parser is **stateful + appending**: it consumes the frame sequence and accumulates one
document, deduping re-shown scrollback and updating the live tail, never duplicating. The
fixtures pin the *final* accumulated document after the whole sequence.

## TranscriptDoc

```jsonc
{
  "harness": "claude_code" | "codex",
  "state":   "awaiting_input" | "working" | "awaiting_approval",
  "condensed": null,                          // ALWAYS null here — see note below
  "segments": [ Segment, … ]
}
```

`state` is read from the frame chrome, not the transcript: idle input box + no spinner =>
`awaiting_input`; spinner / `esc to interrupt` => `working`.

`condensed` is **not** derivable from a deterministic parse — it is produced by a separate
small-LLM summarization pass over `segments`. It is `null` in every deterministic fixture.

## Segment (discriminated on `type`)

```jsonc
// user prompt (de-wrapped: continuation lines joined, single spaces)
{ "type": "user", "text": str }

// assistant prose. phase="final" iff this block is the last assistant text before a
// completion marker (CC "✻ Worked/Baked for …", Codex "─ Worked for …"); otherwise
// "intermediate". elapsed is the marker duration on the final block, else null.
{ "type": "assistant", "phase": "intermediate" | "final", "text": str, "elapsed": str | null }

// a tool invocation. title = the tool/summary line (ctrl+o/ctrl+t suffixes stripped).
// input = the command/args when shown, else null. result = visible output lines when present,
// else null. elided=true when the pane collapsed the result ("+N lines"). running=true while
// in-flight (spinner active, no completion).
{ "type": "tool_call", "title": str, "input": str | null, "result": str | null,
  "elided": bool, "running": bool }
// `result` and `elided` are INDEPENDENT: a tool may show the first output lines
// AND collapse the rest (result set + elided true, e.g. Codex `git status` →
// "M …\n+56 lines"), or be fully collapsed (result null + elided true).

// Codex "Updated Plan" checklist. Emit once per distinct plan state shown (1/6 then 6/6 are
// two segments).
{ "type": "plan_update", "title": str, "items": [ { "done": bool, "text": str }, … ] }

// CC background subagent lifecycle. status "dispatched" when launched, "completed" when done.
{ "type": "agent_event", "name": str, "status": "dispatched" | "completed", "elapsed": str | null }

// live multiple-choice prompt. `selected` tracks the currently highlighted
// option while the prompt is live; `chosen` is the submitted option once answered.
{ "type": "choice_prompt", "question": str,
  "options": [ { "number": int, "label": str, "description": str | null }, … ],
  "footer": str | null, "selected": int, "answered": bool, "chosen": int | null }
```

## Suppressed entirely (never a segment)

Banner / logo / version / cwd · the live input box (`❯ …` / `› …` between the rules,
including mid-typing and placeholders — an unsent prompt is NOT a turn) · status bar
(`⏵⏵ bypass permissions`, `esc to interrupt`, `← for agents`, model/effort) · spinner lines
(`✻ Finagling… (Ns · ↑N tokens · thought for Ns)`) · `Tip:` lines · MCP-startup spam ·
`(ctrl+o to expand)` / `(ctrl+t to view transcript)` suffixes (strip from titles) ·
endless `• Working (Ns)` ticks.

Completion markers (`✻ Worked for 3m 59s`, `─ Worked for 13m 06s ─`) are consumed for
`elapsed` + the final-block flag, then dropped.

## Taste decisions baked in (see parsed_*.html for the canonical render)

1. Tables / diffs are preserved **verbatim** inside `text` (no markdown normalization yet —
   that stays a later, modular pass).
2. Elided output stays elided (`elided:true`, `result:null`) — the pane never held it.
3. A scrolled-off prompt is recovered from earlier frames (that's why the parser accumulates).
