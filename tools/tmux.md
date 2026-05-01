# Tmux Tools

You have access to a tmux tool server. Your session is created and torn down by
the harness — you never manage its lifecycle. State persists across every tool
call you make. One agent, one session, not shared.

The session starts in the project's working directory. Paths you pass to the
shell should be absolute (starting with `/`) unless you have just `cd`'d.
Linux only.

---

## Addressing Model

All tools operate on your assigned session implicitly — there is no `session`
argument. Within the session, work happens in **windows** (tabs). Most tasks
need only window 0; create more when you want parallel workspaces (e.g., a
running server in window 0 while you work in window 1).

```
window    optional — tab index (0-based) or name; defaults to 0
```

Pane splitting is not exposed. Each window has exactly one terminal.

---

## Tools

### WindowNew

```
name:  str | None    (optional label)
```

Opens a new window. Returns the new window index.

```
Returns: "window 2 created"
```

---

### WindowRename

```
name:    str
window:  int | str = 0
```

Sets a label on a window. Useful when several windows are open and you want
`WindowList` to be legible.

---

### WindowList

Lists windows: index, name, current process, idle status.

```
Returns (example):

  0  main      idle   bash
  1  server    busy   uvicorn
  2  logs      idle   bash
```

---

### Run

```
command:  str
window:   int | str       = 0
wait:     float | None    = 120     # seconds
```

Sends `command` + Enter to the window.

- **`wait` is a number** (default 120s): blocks until the shell returns to
  idle or the timeout elapses, then returns cleaned terminal output.
- **`wait=None`**: fires the command and returns immediately. Use for
  servers, watchers, long builds. Inspect later with `Read`.

Idle is detected via tmux's `pane_current_command`, not a sentinel string —
you do not need to add markers to your commands. Calling `Run` on a window
that is already `busy` errors immediately — interrupt the running process
with `Send keys="<C-c>"` first, or use a different window.

Blocking `Run` includes the command's exit code in its return. For
background runs, use `Wait` to retrieve it later.

Output cleaning: ANSI codes stripped, trailing whitespace trimmed per line,
runs of 3+ blank lines collapsed to 2. If output exceeds ~8 KB the return is
the first 100 lines + a truncation notice + the last 50 lines. **To recover
the middle, use `Read` with a larger `lines` value** — the full scrollback is
preserved in tmux regardless of what `Run` returned.

Every response ends with a one-line status footer:

```
[idle | bash | exit 0]      # shell waiting; last command exited 0
[busy | python]             # process running
```

```
Returns (example):

  compiling 3 files...
  build successful in 1.4s

  [idle | bash | exit 0]
```

---

### Wait

```
window:   int | str  = 0
timeout:  float      = 120     # seconds
```

Blocks until the window goes idle, then returns the exit code of the last
command and the tail of its output. Use this to collect the result of a
`Run wait=None` background task.

```
Returns (example):

  ...build log tail...

  [idle | bash | exit 0]
```

If the window is already idle when called, returns immediately with the most
recent exit code. If the timeout elapses while still busy, errors.

---

### Send

```
keys:    str
window:  int | str = 0
```

Sends keystrokes to the window without appending Enter. Use this — not
`Run` — when driving interactive programs (vim, REPLs, prompts).

**Syntax:** every character is sent literally, **except** tokens enclosed in
angle brackets, which are interpreted as named keys:

```
<Enter>  <Esc>  <Tab>  <Space>  <BS>  <Up>  <Down>  <Left>  <Right>
<C-c>    <C-d>  <C-z>  <M-x>    ...
```

So `keys="i hello<Esc>:wq<Enter>"` sends `i`, `h`, `e`, `l`, `l`, `o`, then
Escape, `:`, `w`, `q`, Enter.

If you need to send a literal `<Enter>` as text, split it across two calls
(e.g., `keys="<"` then `keys="Enter>"`) — the parser only fires on a complete
bracketed token within one call.

```
Returns: "sent to window 0"
```

---

### Read

```
lines:   int        = 200
window:  int | str  = 0
```

Captures terminal output. Always returns a status footer (same format as
`Run`). This is how you check progress on background tasks and how you
recover output that `Run` truncated.

`lines` controls scrollback depth. 200 covers most inspection; raise it when
chasing a long build log or a deep traceback. There is no hard cap — request
what you actually need.

```
Returns (example):

  [2026-04-28 14:32:01] worker started
  [2026-04-28 14:32:01] listening on :8080

  [busy | uvicorn]
```

When the window is idle the footer also includes the last exit code:

```
  [idle | bash | exit 0]
```

---

## When to Use What

| Situation | Tool |
|---|---|
| Short command, need result now | `Run` (default `wait=120`) |
| Start a server / long build | `Run` with `wait=None` |
| Check if a background task finished | `Read` — look at the status footer |
| Wait on a background task and get its exit code | `Wait` |
| Recover the middle of a truncated `Run` log | `Read` with larger `lines` |
| Drive vim, a REPL, an interactive prompt | `Send` + `Read` |
| Run something parallel to other work | `WindowNew`, then `Run wait=None` there |
| Survey all windows | `WindowList` |
| Interrupt a runaway background process | `Send keys="<C-c>"` |

## Idle Detection

The status footer is the canonical signal. `[idle | bash]` means the shell is
waiting; `[busy | X]` means process X is running. Blocking `Run` handles
this internally — do not build your own polling loop.

## Errors

Errors are returned as the tool result, not raised. They are one short line
describing what's wrong:

```
error: window 5 does not exist
error: window 0 is busy (uvicorn) — interrupt or use another window
error: Wait timed out after 120s; window still busy (pytest)
error: malformed key token "<Entr>" in keys
```

Read the message and adjust — do not retry the identical call.

## Output Discipline

`Run` truncates above ~8 KB to keep responses small; `Read` does not. If a
command produces a lot of output and you only care about a slice, redirect to
a file (`cmd > /tmp/out 2>&1`) and inspect with `sed`/`grep` rather than
piping everything through the agent's context.
