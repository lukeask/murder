"""Context-index SQLite schema (experimental, separate from murder.db).

Invariants encoded here and in repository code
----------------------------------------------
* ``state_timestamp`` identifies the on-disk repository state a snapshot
  represents. ``generated_at`` records when indexing finished. Newest-state
  selection ALWAYS uses ``state_timestamp DESC, snapshot_id DESC`` — never
  ``generated_at``. An older index that finishes later must not supersede a
  newer repository state.
* Logical rows (``files``, ``semantic_units``) are stable identities. Content
  and extraction rows (``file_versions``, ``semantic_unit_versions``, imports,
  references, relationships, resource links) are versioned against exact
  file contents. Unchanged files reuse the same file version and all of its
  child extraction rows across snapshots.
* Relationship / reference *sources* point at exact versions (where evidence
  was observed). *Targets* normally point at logical identities so edges
  survive ordinary edits and line movement.
* Local extraction edges live in ``relationships`` / ``reference_targets``
  (file-version scoped, reusable with the file version). Cross-file
  resolutions live in ``resolved_relationships`` / ``resolved_reference_targets``
  and always carry an explicit ``snapshot_id`` — an unchanged file can resolve
  differently when its neighbours change.
* Confidence is one of ``exact`` / ``inferred`` / ``weak``, never a float
  probability.
* Full source file bodies are never stored for indexing. Step 6's evidence
  ledger stores only the exact bounded excerpts that were supplied to a
  recipient (for focused diffs and restart survival) — never whole files.
  Final evidence ranges are still read from the live worktree by the
  exact-evidence kernel.
* Only the newest two ``ready`` snapshots per worktree are retained; older
  ready snapshots are pruned and unreachable versions garbage-collected.
  ``building`` / ``failed`` snapshots do not count toward the two-ready budget.
"""

from __future__ import annotations

# Bump when the experimental DDL shape changes incompatibly.
SCHEMA_VERSION = 3

# fmt: off
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS context_index_schema (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version  INTEGER NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worktrees (
    worktree_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_root   TEXT NOT NULL,
    worktree_root     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,

    UNIQUE (repository_root, worktree_root)
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    worktree_id       INTEGER NOT NULL
                      REFERENCES worktrees(worktree_id)
                      ON DELETE CASCADE,
    state_timestamp   TEXT NOT NULL,
    commit_sha        TEXT,
    status            TEXT NOT NULL
                      CHECK (status IN ('building', 'ready', 'failed')),
    generated_at      TEXT NOT NULL,
    failure_reason    TEXT,

    CHECK (
        (status = 'failed' AND failure_reason IS NOT NULL)
        OR
        (status != 'failed')
    )
);

CREATE INDEX IF NOT EXISTS idx_snapshots_worktree_state
    ON snapshots(worktree_id, state_timestamp DESC, snapshot_id DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_worktree_ready
    ON snapshots(worktree_id, state_timestamp DESC, snapshot_id DESC)
    WHERE status = 'ready';

CREATE INDEX IF NOT EXISTS idx_snapshots_status
    ON snapshots(status);

CREATE TABLE IF NOT EXISTS files (
    file_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    worktree_id      INTEGER NOT NULL
                     REFERENCES worktrees(worktree_id)
                     ON DELETE CASCADE,
    path             TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,

    UNIQUE (worktree_id, path)
);

CREATE INDEX IF NOT EXISTS idx_files_worktree
    ON files(worktree_id);

CREATE TABLE IF NOT EXISTS file_versions (
    file_version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id            INTEGER NOT NULL
                       REFERENCES files(file_id)
                       ON DELETE CASCADE,
    source_hash        TEXT NOT NULL,
    language           TEXT,
    byte_count         INTEGER NOT NULL,
    line_count         INTEGER NOT NULL,
    parse_status       TEXT NOT NULL
                       CHECK (parse_status IN (
                           'parsed',
                           'partial',
                           'text_only',
                           'unsupported',
                           'failed'
                       )),
    parse_error        TEXT,
    extractor_version  TEXT NOT NULL,
    indexed_at         TEXT NOT NULL,

    UNIQUE (file_id, source_hash, extractor_version)
);

CREATE INDEX IF NOT EXISTS idx_file_versions_file
    ON file_versions(file_id);

CREATE TABLE IF NOT EXISTS snapshot_files (
    snapshot_id       INTEGER NOT NULL
                      REFERENCES snapshots(snapshot_id)
                      ON DELETE CASCADE,
    file_id           INTEGER NOT NULL
                      REFERENCES files(file_id)
                      ON DELETE CASCADE,
    file_version_id   INTEGER NOT NULL
                      REFERENCES file_versions(file_version_id)
                      ON DELETE RESTRICT,

    PRIMARY KEY (snapshot_id, file_id),
    UNIQUE (snapshot_id, file_version_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_files_version
    ON snapshot_files(file_version_id);

CREATE TABLE IF NOT EXISTS semantic_units (
    unit_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id          INTEGER NOT NULL
                     REFERENCES files(file_id)
                     ON DELETE CASCADE,
    logical_key      TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,

    UNIQUE (file_id, logical_key)
);

CREATE INDEX IF NOT EXISTS idx_semantic_units_file
    ON semantic_units(file_id);

CREATE TABLE IF NOT EXISTS semantic_unit_versions (
    unit_version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id            INTEGER NOT NULL
                       REFERENCES semantic_units(unit_id)
                       ON DELETE CASCADE,
    file_version_id    INTEGER NOT NULL
                       REFERENCES file_versions(file_version_id)
                       ON DELETE CASCADE,

    language_kind      TEXT NOT NULL,
    semantic_role      TEXT,
    qualified_name     TEXT NOT NULL,
    unqualified_name   TEXT NOT NULL,
    signature          TEXT,
    start_line         INTEGER NOT NULL,
    end_line           INTEGER NOT NULL,
    parent_unit_id     INTEGER
                       REFERENCES semantic_units(unit_id)
                       ON DELETE SET NULL,
    exported           INTEGER NOT NULL DEFAULT 0
                       CHECK (exported IN (0, 1)),
    metadata_json      TEXT NOT NULL DEFAULT '{}',

    CHECK (start_line >= 1),
    CHECK (end_line >= start_line),

    UNIQUE (unit_id, file_version_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_unit_versions_file_version
    ON semantic_unit_versions(file_version_id);

CREATE INDEX IF NOT EXISTS idx_semantic_unit_versions_unit
    ON semantic_unit_versions(unit_id);

CREATE TABLE IF NOT EXISTS imports (
    import_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_version_id        INTEGER NOT NULL
                           REFERENCES file_versions(file_version_id)
                           ON DELETE CASCADE,
    source_unit_version_id INTEGER
                           REFERENCES semantic_unit_versions(unit_version_id)
                           ON DELETE CASCADE,

    module_specifier       TEXT NOT NULL,
    imported_name          TEXT,
    local_alias            TEXT,
    import_kind            TEXT NOT NULL,
    start_line             INTEGER NOT NULL,
    end_line               INTEGER NOT NULL,
    metadata_json          TEXT NOT NULL DEFAULT '{}',

    CHECK (start_line >= 1),
    CHECK (end_line >= start_line)
);

CREATE INDEX IF NOT EXISTS idx_imports_file_version
    ON imports(file_version_id);

-- ``references`` is a SQL keyword; always quote the table name in statements.
CREATE TABLE IF NOT EXISTS "references" (
    reference_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_version_id        INTEGER NOT NULL
                           REFERENCES file_versions(file_version_id)
                           ON DELETE CASCADE,
    source_unit_version_id INTEGER
                           REFERENCES semantic_unit_versions(unit_version_id)
                           ON DELETE CASCADE,

    identifier             TEXT NOT NULL,
    reference_kind         TEXT NOT NULL,
    start_line             INTEGER NOT NULL,
    end_line               INTEGER NOT NULL,
    resolution_method      TEXT,
    ambiguity_count        INTEGER NOT NULL DEFAULT 0,
    metadata_json          TEXT NOT NULL DEFAULT '{}',

    CHECK (start_line >= 1),
    CHECK (end_line >= start_line),
    CHECK (ambiguity_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_references_file_version
    ON "references"(file_version_id);

-- Local (same-file) resolution candidates attached at extraction time.
-- Cross-file targets belong in resolved_reference_targets with snapshot_id.
CREATE TABLE IF NOT EXISTS reference_targets (
    reference_id       INTEGER NOT NULL
                       REFERENCES "references"(reference_id)
                       ON DELETE CASCADE,
    target_unit_id     INTEGER NOT NULL
                       REFERENCES semantic_units(unit_id)
                       ON DELETE CASCADE,
    confidence         TEXT NOT NULL
                       CHECK (confidence IN ('exact', 'inferred', 'weak')),
    is_preferred       INTEGER NOT NULL DEFAULT 0
                       CHECK (is_preferred IN (0, 1)),
    resolution_method  TEXT NOT NULL,

    PRIMARY KEY (reference_id, target_unit_id)
);

-- Snapshot-scoped cross-file reference resolutions. Reading without
-- snapshot_id is impossible: the column is NOT NULL and every query API
-- requires it. Reused file_versions do not reuse these rows.
CREATE TABLE IF NOT EXISTS resolved_reference_targets (
    snapshot_id        INTEGER NOT NULL
                       REFERENCES snapshots(snapshot_id)
                       ON DELETE CASCADE,
    reference_id       INTEGER NOT NULL
                       REFERENCES "references"(reference_id)
                       ON DELETE CASCADE,
    target_unit_id     INTEGER NOT NULL
                       REFERENCES semantic_units(unit_id)
                       ON DELETE CASCADE,
    confidence         TEXT NOT NULL
                       CHECK (confidence IN ('exact', 'inferred', 'weak')),
    is_preferred       INTEGER NOT NULL DEFAULT 0
                       CHECK (is_preferred IN (0, 1)),
    resolution_method  TEXT NOT NULL,

    PRIMARY KEY (snapshot_id, reference_id, target_unit_id)
);

CREATE INDEX IF NOT EXISTS idx_resolved_reference_targets_snapshot
    ON resolved_reference_targets(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_resolved_reference_targets_reference
    ON resolved_reference_targets(snapshot_id, reference_id);

-- Local extraction relationships (contains, same-file calls, …). Reusable
-- with the file version. Cross-file edges belong in resolved_relationships.
CREATE TABLE IF NOT EXISTS relationships (
    relationship_id         INTEGER PRIMARY KEY AUTOINCREMENT,

    source_file_version_id  INTEGER NOT NULL
                            REFERENCES file_versions(file_version_id)
                            ON DELETE CASCADE,
    source_unit_version_id  INTEGER
                            REFERENCES semantic_unit_versions(unit_version_id)
                            ON DELETE CASCADE,

    target_file_id          INTEGER
                            REFERENCES files(file_id)
                            ON DELETE CASCADE,
    target_unit_id          INTEGER
                            REFERENCES semantic_units(unit_id)
                            ON DELETE CASCADE,

    relation_kind           TEXT NOT NULL,
    start_line              INTEGER,
    end_line                INTEGER,
    confidence              TEXT NOT NULL
                            CHECK (confidence IN ('exact', 'inferred', 'weak')),
    resolution_method       TEXT NOT NULL,
    metadata_json           TEXT NOT NULL DEFAULT '{}',

    CHECK (target_file_id IS NOT NULL OR target_unit_id IS NOT NULL),
    CHECK (
        (start_line IS NULL AND end_line IS NULL)
        OR
        (start_line >= 1 AND end_line >= start_line)
    )
);

CREATE INDEX IF NOT EXISTS idx_relationships_source_file_version
    ON relationships(source_file_version_id);

CREATE INDEX IF NOT EXISTS idx_relationships_target_unit
    ON relationships(target_unit_id);

CREATE INDEX IF NOT EXISTS idx_relationships_target_file
    ON relationships(target_file_id);

-- Snapshot-scoped cross-file relationships written by the resolver.
-- Unscoped reads are impossible: snapshot_id is NOT NULL.
CREATE TABLE IF NOT EXISTS resolved_relationships (
    relationship_id         INTEGER PRIMARY KEY AUTOINCREMENT,

    snapshot_id             INTEGER NOT NULL
                            REFERENCES snapshots(snapshot_id)
                            ON DELETE CASCADE,

    source_file_version_id  INTEGER NOT NULL
                            REFERENCES file_versions(file_version_id)
                            ON DELETE CASCADE,
    source_unit_version_id  INTEGER
                            REFERENCES semantic_unit_versions(unit_version_id)
                            ON DELETE CASCADE,

    target_file_id          INTEGER
                            REFERENCES files(file_id)
                            ON DELETE CASCADE,
    target_unit_id          INTEGER
                            REFERENCES semantic_units(unit_id)
                            ON DELETE CASCADE,

    relation_kind           TEXT NOT NULL,
    start_line              INTEGER,
    end_line                INTEGER,
    confidence              TEXT NOT NULL
                            CHECK (confidence IN ('exact', 'inferred', 'weak')),
    resolution_method       TEXT NOT NULL,
    metadata_json           TEXT NOT NULL DEFAULT '{}',

    CHECK (target_file_id IS NOT NULL OR target_unit_id IS NOT NULL),
    CHECK (
        (start_line IS NULL AND end_line IS NULL)
        OR
        (start_line >= 1 AND end_line >= start_line)
    )
);

CREATE INDEX IF NOT EXISTS idx_resolved_relationships_snapshot
    ON resolved_relationships(snapshot_id);

CREATE INDEX IF NOT EXISTS idx_resolved_relationships_source
    ON resolved_relationships(snapshot_id, source_file_version_id);

CREATE INDEX IF NOT EXISTS idx_resolved_relationships_target_unit
    ON resolved_relationships(snapshot_id, target_unit_id);

CREATE INDEX IF NOT EXISTS idx_resolved_relationships_target_file
    ON resolved_relationships(snapshot_id, target_file_id);

CREATE TABLE IF NOT EXISTS resource_links (
    resource_link_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_unit_version_id INTEGER NOT NULL
                           REFERENCES semantic_unit_versions(unit_version_id)
                           ON DELETE CASCADE,
    target_file_id         INTEGER
                           REFERENCES files(file_id)
                           ON DELETE CASCADE,
    unresolved_path        TEXT,
    resource_kind          TEXT NOT NULL,
    start_line             INTEGER,
    end_line               INTEGER,
    metadata_json          TEXT NOT NULL DEFAULT '{}',

    CHECK (target_file_id IS NOT NULL OR unresolved_path IS NOT NULL),
    CHECK (
        (start_line IS NULL AND end_line IS NULL)
        OR
        (start_line >= 1 AND end_line >= start_line)
    )
);

CREATE INDEX IF NOT EXISTS idx_resource_links_source_unit_version
    ON resource_links(source_unit_version_id);

CREATE INDEX IF NOT EXISTS idx_resource_links_target_file
    ON resource_links(target_file_id);

-- Step 6: agent-local evidence ledger (session-scoped, not crow-ID keyed).
-- Lifetime follows the session, not two-snapshot index retention.
CREATE TABLE IF NOT EXISTS evidence_scopes (
    scope_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_root     TEXT NOT NULL,
    worktree_root       TEXT NOT NULL,
    recipient_id        TEXT NOT NULL,
    session_id          TEXT,
    conversation_id     TEXT,
    created_at          TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,

    UNIQUE (
        repository_root,
        worktree_root,
        recipient_id,
        session_id,
        conversation_id
    )
);

CREATE INDEX IF NOT EXISTS idx_evidence_scopes_session
    ON evidence_scopes(session_id);

CREATE INDEX IF NOT EXISTS idx_evidence_scopes_recipient
    ON evidence_scopes(recipient_id, repository_root, worktree_root);

CREATE TABLE IF NOT EXISTS evidence_blobs (
    content_hash    TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    line_count      INTEGER NOT NULL,
    created_at      TEXT NOT NULL,

    CHECK (line_count >= 0)
);

CREATE TABLE IF NOT EXISTS evidence_ledger_entries (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id        INTEGER NOT NULL
                    REFERENCES evidence_scopes(scope_id)
                    ON DELETE CASCADE,
    delivery_id     TEXT NOT NULL,
    path            TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    source_hash     TEXT NOT NULL,
    content_hash    TEXT NOT NULL
                    REFERENCES evidence_blobs(content_hash)
                    ON DELETE RESTRICT,
    category        TEXT NOT NULL,
    payload_kind    TEXT NOT NULL
                    CHECK (payload_kind IN ('source', 'diff')),
    status          TEXT NOT NULL
                    CHECK (status IN ('prepared', 'supplied', 'abandoned')),
    prepared_at     TEXT NOT NULL,
    supplied_at     TEXT,

    CHECK (start_line >= 1),
    CHECK (end_line >= start_line),
    CHECK (
        (status = 'supplied' AND supplied_at IS NOT NULL)
        OR
        (status != 'supplied')
    )
);

CREATE INDEX IF NOT EXISTS idx_evidence_ledger_scope_status
    ON evidence_ledger_entries(scope_id, status);

CREATE INDEX IF NOT EXISTS idx_evidence_ledger_delivery
    ON evidence_ledger_entries(delivery_id);

CREATE INDEX IF NOT EXISTS idx_evidence_ledger_content_hash
    ON evidence_ledger_entries(content_hash);
"""
# fmt: on
