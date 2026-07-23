"""Runtime-owned report file synchronization.

``ReportSync`` is a thin factory wrapper over ``SimpleDocSync`` — the shared
reconcile algorithm lives there.  Reports are structural twins of notes:
plain markdown bodies, no frontmatter, full revision tracking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from murder.state.persistence import reports as _reports_db
from murder.state.storage.paths import report_md, reports_dir
from murder.work.simple_doc_sync import SimpleDocSync


def ReportSync(
    repo_root: Path,
    db: Any,
    *,
    poll_s: float = 1.5,
    debounce_s: float = 0.75,
) -> SimpleDocSync:
    """Return a ``SimpleDocSync`` configured for ``.murder/reports/*.md``.

    File reconciliation writes the authoritative report rows directly.
    """
    return SimpleDocSync(
        repo_root,
        db,
        dir_fn=reports_dir,
        md_path_fn=report_md,
        list_fn=_reports_db.list_reports,
        get_fn=_reports_db.get_report,
        upsert_fn=_reports_db.upsert_report,
        insert_revision_fn=_reports_db.insert_report_revision,
        poll_s=poll_s,
        debounce_s=debounce_s,
    )
