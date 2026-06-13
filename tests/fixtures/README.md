# Fixtures

Static captured inputs live here:

- terminal panes
- transcripts
- sample markdown or YAML
- serialized API responses

Keep fixtures small and named for the behavior they exercise. When a fixture is
captured from a real tool, add a short note with the tool/version/date near the
fixture or in the test that uses it.

Expected subdirectories:

- `harness_panes/` — tmux pane snapshots for harness adapter unit tests
  (including the cursor grammar fixtures). Regenerate from local recordings via
  `python tools/testing/extract_fixtures.py` (source:
  `tools/testing/recordings/`).
- `harness_state/` — manifest + frames recordings per harness.
- `transcripts/` — parsed transcript ground-truth (see `transcripts/SCHEMA.md`).
