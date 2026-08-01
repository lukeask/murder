"""Fixture materialization and builtin evaluation cases.

Fixtures live under ``tests/fixtures/context_compiler/``, organised by
resolution shape rather than language.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from murder.context_compiler.eval.cases import EvalCase, RangeRef, UnitRef
from murder.context_compiler.models import RecipientProfile

# Repo-relative fixtures root (resolved from this file's location).
_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_ROOT = _REPO_ROOT / "tests" / "fixtures" / "context_compiler"


def materialize_fixture_repo(shape: str, dest: Path) -> Path:
    """Copy a fixture shape directory into ``dest`` and return the worktree root.

    Special-case ``snapshot_drift``: materializes snapshot-A layout with
    ``target.py`` taken from ``target_a.py``. Callers that need snapshot B
    overwrite ``target.py`` themselves.
    """
    src = FIXTURES_ROOT / shape
    if not src.is_dir():
        raise FileNotFoundError(f"unknown fixture shape: {shape}")
    dest.mkdir(parents=True, exist_ok=True)
    if shape == "snapshot_drift":
        shutil.copy2(src / "source.py", dest / "source.py")
        shutil.copy2(src / "target_a.py", dest / "target.py")
        return dest
    if shape.startswith("frameworks/"):
        # Already a leaf shape path like frameworks/react
        for path in src.iterdir():
            if path.is_file():
                shutil.copy2(path, dest / path.name)
        return dest
    for path in src.iterdir():
        if path.is_file():
            shutil.copy2(path, dest / path.name)
        elif path.is_dir():
            shutil.copytree(path, dest / path.name, dirs_exist_ok=True)
    return dest


def materialize_framework(name: str, dest: Path) -> Path:
    return materialize_fixture_repo(f"frameworks/{name}", dest)


def all_builtin_cases() -> tuple[EvalCase, ...]:
    """Every Step 3 fixture shape as an evaluation case, plus Step 4 corpus cases."""
    return (
        EvalCase(
            name="cross-file-import-and-alias",
            objective="Call the shared helper through its import alias.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="cross_file",
            symbol_hints=("helper", "do_help", "shared_name"),
            path_hints=("consumer.py",),
            expected=(
                UnitRef("util.py", "helper"),
                UnitRef("consumer.py", "run"),
            ),
            forbidden=(UnitRef("noise.py", "distractor"),),
            top_k=20,
        ),
        EvalCase(
            name="unresolved-missing-symbol",
            objective="Investigate TotallyMissingSymbol usage.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="unresolved",
            symbol_hints=("TotallyMissingSymbol", "call_missing"),
            path_hints=("caller.py",),
            expected=(UnitRef("caller.py", "call_missing"),),
            forbidden=(),
            top_k=20,
        ),
        EvalCase(
            name="tests-explicit-and-filename",
            objective="Fix widget_save and review gadget tests.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="tests",
            symbol_hints=("widget_save", "gadget_run"),
            path_hints=("widget.py", "gadget.py"),
            # Explicit import evidence should surface the focused widget test;
            # gadget remains filename-only affinity and is not required here.
            expected=(
                UnitRef("widget.py", "widget_save"),
                UnitRef("test_widget.py", "test_widget_save"),
            ),
            forbidden=(),
            top_k=20,
        ),
        EvalCase(
            name="snapshot-drift-source",
            objective="Update Greeter usage in source.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="snapshot_drift",
            symbol_hints=("Greeter", "greet"),
            path_hints=("source.py",),
            expected=(
                UnitRef("source.py", "greet"),
                UnitRef("target.py", "Greeter"),
            ),
            forbidden=(),
            top_k=20,
        ),
        EvalCase(
            name="react-profile-editor",
            objective="Add validation to ProfileEditor save behavior.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="frameworks/react",
            symbol_hints=("ProfileEditor",),
            path_hints=("ProfileEditor.tsx",),
            expected=(
                UnitRef("ProfileEditor.tsx", "ProfileEditor"),
                UnitRef("ProfileEditor.test.tsx", "savesValidProfile"),
            ),
            forbidden=(UnitRef("AdminDashboard.tsx", "AdminDashboard"),),
            top_k=20,
        ),
        EvalCase(
            name="vue-profile-editor",
            objective="Adjust ProfileEditor template bindings.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="frameworks/vue",
            symbol_hints=("ProfileEditor", "ProfileCard"),
            path_hints=("ProfileEditor.vue",),
            expected=(
                UnitRef("ProfileEditor.vue", "ProfileEditor"),
                UnitRef("ProfileCard.vue", "ProfileCard"),
            ),
            forbidden=(),
            top_k=20,
        ),
        EvalCase(
            name="svelte-profile-editor",
            objective="Adjust ProfileEditor markup.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="frameworks/svelte",
            symbol_hints=("ProfileEditor", "ProfileCard"),
            path_hints=("ProfileEditor.svelte",),
            expected=(
                UnitRef("ProfileEditor.svelte", "ProfileEditor"),
                UnitRef("ProfileCard.svelte", "ProfileCard"),
            ),
            forbidden=(),
            top_k=20,
        ),
        EvalCase(
            name="angular-profile-editor",
            objective="Wire ProfileEditorComponent template.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="frameworks/angular",
            symbol_hints=("ProfileEditorComponent", "app-profile-editor"),
            path_hints=("profile-editor.component.ts",),
            expected=(
                UnitRef("profile-editor.component.ts", "ProfileEditorComponent"),
                UnitRef("profile-card.component.ts", "ProfileCardComponent"),
            ),
            forbidden=(),
            top_k=20,
        ),
        # --- Step 4 corpus cases ---
        EvalCase(
            name="ranking-compact-profile-editor",
            objective="Fix ProfileEditor.save validation.",
            profile=RecipientProfile.COMPACT,
            fixture_shape="ranking",
            symbol_hints=("ProfileEditor", "save"),
            path_hints=("editor.py",),
            expected=(UnitRef("editor.py", "ProfileEditor"),),
            forbidden=(UnitRef("hub.py", "common_util_alpha"),),
            top_k=10,
            mode="corpus",
            expect_fewer_tokens_than="ranking-implementation-profile-editor",
        ),
        EvalCase(
            name="ranking-implementation-profile-editor",
            objective="Fix ProfileEditor.save validation.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="ranking",
            symbol_hints=("ProfileEditor", "save"),
            path_hints=("editor.py",),
            expected=(
                UnitRef("editor.py", "ProfileEditor"),
                UnitRef("test_editor.py", "test_save_profile"),
            ),
            forbidden=(UnitRef("hub.py", "common_util_alpha"),),
            top_k=20,
            mode="corpus",
        ),
        EvalCase(
            name="ranking-planning-profile-editor",
            objective="Plan changes to ProfileEditor save and its public contract.",
            profile=RecipientProfile.PLANNING,
            fixture_shape="ranking",
            symbol_hints=("ProfileEditor", "ProfileContract", "public_save_api"),
            path_hints=("editor.py", "contracts.py"),
            expected=(
                UnitRef("editor.py", "ProfileEditor"),
                UnitRef("contracts.py", "ProfileContract"),
            ),
            forbidden=(UnitRef("hub.py", "common_util_alpha"),),
            top_k=30,
            mode="corpus",
        ),
        EvalCase(
            name="ranking-lexical-inside-function",
            objective="Locate magic_validation_token handling.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="ranking",
            symbol_hints=("magic_validation_token",),
            path_hints=("lexical_target.py",),
            expected=(UnitRef("lexical_target.py", "process_payload"),),
            forbidden=(UnitRef("lexical_target.py", "unrelated_top"),),
            top_k=10,
            mode="corpus",
        ),
        EvalCase(
            name="ranking-angular-template-region",
            objective="Wire ProfileEditorComponent template region.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="frameworks/angular",
            symbol_hints=("ProfileEditorComponent",),
            path_hints=("profile-editor.component.ts",),
            expected=(UnitRef("profile-editor.component.ts", "ProfileEditorComponent"),),
            expected_ranges=(RangeRef("profile-editor.component.html", 1, 3),),
            top_k=20,
            mode="corpus",
        ),
        # --- Step 5 graded cases (fake graders; no model calls) ---
        EvalCase(
            name="grading-exclude-hub",
            objective="Fix ProfileEditor.save validation.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="ranking",
            symbol_hints=("ProfileEditor", "save"),
            path_hints=("editor.py",),
            expected=(
                UnitRef("editor.py", "ProfileEditor"),
                UnitRef("test_editor.py", "test_save_profile"),
            ),
            forbidden=(UnitRef("hub.py", "common_util_alpha"),),
            top_k=20,
            mode="graded",
            grader_exclude_paths=("hub.py",),
            expect_expansion_rounds=0,
        ),
        EvalCase(
            name="grading-search-terms-expansion",
            objective="Inspect the nearby module layout.",
            profile=RecipientProfile.IMPLEMENTATION,
            fixture_shape="ranking",
            # Bland seeds so Step 4 alone misses the lexical needle; gaps supply it.
            symbol_hints=(),
            path_hints=("editor.py",),
            expected=(UnitRef("lexical_target.py", "process_payload"),),
            forbidden=(UnitRef("hub.py", "common_util_alpha"),),
            top_k=20,
            mode="graded",
            grader_gap_search_terms=("magic_validation_token",),
            expect_expansion_rounds=1,
        ),
    )


def all_candidate_cases() -> tuple[EvalCase, ...]:
    """Step 3 candidate-mode cases — the default ``run_cases()`` suite.

    Excludes Step 4 corpus and Step 5 graded cases; use ``all_corpus_cases()`` /
    ``all_graded_cases()`` for those suites.
    """
    return tuple(c for c in all_builtin_cases() if c.mode == "candidates")


def all_corpus_cases() -> tuple[EvalCase, ...]:
    """Step 4 corpus-mode cases only (pass explicitly to ``run_cases``)."""
    return tuple(c for c in all_builtin_cases() if c.mode == "corpus")


def all_graded_cases() -> tuple[EvalCase, ...]:
    """Step 5 graded-mode cases only (fake graders; pass explicitly to ``run_cases``)."""
    return tuple(c for c in all_builtin_cases() if c.mode == "graded")


__all__ = [
    "FIXTURES_ROOT",
    "all_builtin_cases",
    "all_candidate_cases",
    "all_corpus_cases",
    "all_graded_cases",
    "materialize_fixture_repo",
    "materialize_framework",
]
