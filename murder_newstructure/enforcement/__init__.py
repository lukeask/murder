"""Write-set enforcement (D5).

Two layers:
- `watcher.py`  — live filesystem watcher (inotify via watchfiles); emits
  escalation on writes outside any active crow's write_set.
- `git_diff.py` — post-hoc diff against the crow's start_commit; final
  pass/fail gate before status → done.
"""
