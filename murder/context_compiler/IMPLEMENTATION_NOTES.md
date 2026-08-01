# Context Assembler 2 — V1 implementation notes

Structural indexing + deterministic candidate generation for
`.murder/context-index.db`. No PageRank, vector search, brief rendering,
`/compact`, or CodingCrow integration.

## What works

- Normalized extractors (Python AST; tree-sitter JS/TS, Rust, C/C++, Go, HTML, CSS/SCSS/LESS) with fail-soft grammar loading.
- Framework enrichment: React (multi-component files), Vue/Svelte SFC aggregates, Angular `@Component` resource links.
- Incremental `index_worktree` / `index_worktree_sync`: content-hash + extractor-version reuse, two-snapshot retention, repository-level import/reference resolution.
- Candidate providers: exact hints, lexical, active diff, tests, one-hop structural, composite merge/bounds.
- Step 3: snapshot-scoped `resolved_*` tables, confidence tiers, eval harness.
- Step 4: `CorpusProposer.propose_corpus(request, snapshot)` — profile-weighted ranking, bounded expansion, exact range shaping, token budgets. Eval corpus mode covers profile comparison, lexical/test/template shaping.
- Step 5: `CorpusGrader.grade_corpus(request, proposal, snapshot)` — cheap-model grading via `ContextGrader.grade`, profile rubrics, preview rendering, deterministic post-validation, one expansion round as `RequestDelta` hints, invalid-output fallback to Step 4. Domain types stay LLM-free; `LlmContextGrader` adapts `APIClient` + policy resolution. Eval graded mode (`all_graded_cases` / `mode="graded"`) measures post-grade recall, forbidden hits, and harness determinism with fake graders.
- Step 6: agent-local evidence ledger in `context-index.db` (`evidence_scopes` / `evidence_blobs` / `evidence_ledger_entries`, schema v3). Two-phase `prepare_entries` → `mark_supplied`/`mark_abandoned`; `plan_evidence` subtracts matching hashes, emits focused diffs on hash change, and deletion notices for gone counterparts. Session-scoped lifetime independent of snapshot GC.

## Deviations / design choices

- **Unresolved relationships are not persisted.** Persistence requires `target_file_id` or `target_unit_id`. Same-file edges resolve via `metadata.target_logical_key` at insert time; cross-file inherits/calls stay in extractor metadata until the resolver (or a later pass) can attach concrete targets. Import edges are re-derived from the imports table during resolution.
- **SFC style/template resource links** often point at the SFC path itself (self-links) rather than exploded child files; Angular external `templateUrl` / `styleUrls` remain path-based until the target file is in the snapshot.
- **Named CSS/HTML entities only.** Ordinary tags and selectors are not exploded into units (custom properties, keyframes, ids, and similar named entities may still appear).
- **JS/TS path aliases** (tsconfig `paths`) are not resolved; only relative/path-like import forms are best-effort matched.
- Step 0 `rendering.py` / evidence helpers remain for prior brief assembly experiments; candidate providers do not call them and do not emit recipient-visible prose.
- **Step 4** publishes `RangeProposal` / `CorpusProposal` only — no `CandidateScore` component breakdown. Exclusions live on `RankingTrace`, not on the proposal. Ranking merge uses `ranking_identity` (kind + range aware), not unit-id alone.
- **Step 5** merges all ``RequestDelta`` fields into ``ContextRequest`` for re-propose: ``path_hints``, ``symbol_hints``, distinct ``search_terms`` (lexical provider), and ``relationship_kind_hints`` (Step 4 expansion + structural filter). Adequacy and item grades share one model call (``GradeResult.gaps``). Post-validation reshapes via Step 4 ``reshape_proposal_by_category``. ``ContextGrader`` is defined once in ``grading.ports`` and re-exported from package ``ports``. Malformed structured output: ``LlmContextGrader`` retries once with the validation error; ``CorpusGrader`` does not stack a second retry (two attempts total, then Step 4 fallback). Preview headers include merged ``search_terms`` / ``relationship_kind_hints`` for round-2 grading. Cross-file ``calls`` auto-resolution remains a Step 3/indexer limitation — expansion tests may inject resolved caller edges.
- **Step 6** extends `EvidenceLedgerEntry` rather than inventing a parallel record. Scope is repository/worktree + recipient + session/conversation (not crow ID). Diff format lives in `ledger/diff.py` so it can change without touching store semantics. Ledger loads leave Step-0-only fields (`reason`, `recipient_profile`, `operation_id`, cut `later_*` flags) at defaults — persisted identity is `category` / `delivery_id` / status. Recipient rendering covers SOURCE, DIFF, and deletion notices via `render_evidence_segment` / `render_deletion_notice`.

## Part 12 — known limitations (honest)

These produce lower confidence, ambiguity, `partial` status, or diagnostics — not fabricated certainty:

- **Dynamic dispatch / reflection** — not modeled; references stay unresolved or ambiguous
- **Generated / macro-heavy code** — often `partial` or text-only; macros not expanded
- **Complex C++ templates** — shallow structural extraction only
- **Ambiguous textual identifiers** — capped fan-out; multi-target left ambiguous (no preferred pick)
- **JS dynamic `import()`** — specifier may be missing; no runtime evaluation
- **TS path aliases** — unresolved without a resolvable relative path
- **Vue macros** (`defineProps`, etc.) — best-effort; edge cases → partial / missing units
- **Angular DI** — decorator metadata/resource links only; no injector graph
- **Svelte compiler transforms** — source-level script/template/style only
- **External generated templates** — linked by path when present; no codegen tracing
- **Conditional compilation** — not evaluated (`#ifdef`, feature flags, etc.)
- **One-/two-hop expansion caps** — guesses; tune against the eval harness
- **Grading expansion** — exactly one round; remaining gaps become unresolved questions

## Smoke index (Murder repo)

Typical successful run against this repository (orders of magnitude):

- ~1.2k indexable files parsed, ~1.3k discovered (incl. text-only), full reuse on unchanged re-index
- Tens of thousands of units / imports / references / relationships
- Resolution fills import→file and many reference targets; ambiguous names skipped

## Out of scope (intentionally)

PageRank, vectors, worker registration, BriefAssembler/CodingCrow wiring
(Steps 7–8), removal of `murder/codebase_map`.
