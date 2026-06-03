# Testing tools

This directory is for standalone development utilities related to testing:

- fixture capture scripts
- transcript normalization
- replay/probe commands
- one-off migration helpers for test data

If pytest imports the code directly, it should usually live under
`tests/support/` instead.

## tmux recorder

`tmux_record.py` is a manual capture utility for harness tuning. It wraps a real
`tmux attach-session` client in a PTY and records:

- raw stdin bytes you typed
- raw stdout bytes emitted by the tmux client
- periodic `tmux capture-pane` snapshots

Basic usage:

```bash
python tools/testing/tmux_record.py --session codex-demo -- codex --model gpt-5.5
```

If `--session` does not exist, the recorder creates it in detached mode using
the current terminal size and then attaches. If the session already exists, the
recorder just attaches and logs.

When you detach or the session exits, the recorder prompts to save or discard
the capture. Saved recordings land under `tools/testing/recordings/` with:

- `metadata.json`
- `events.jsonl`
- `frames.jsonl`

Useful flags:

- `--cwd PATH` sets the working directory for a new session.
- `--label NAME` adds a readable suffix to the recording directory name.
- `--keep-session` leaves a newly created session alive after recording ends.
- `--frame-interval 0.2` lowers frame sampling frequency (default is 20 Hz).
