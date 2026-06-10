"""Tests for the HARNESSES_AND_MODELS.md generator (C10 / B9).

Covers the pure renderer (harness names, model labels, effort levels derived
from the adapter classvars, empty-model handling) and the I/O helper writing the
doc from the live model cache.
"""

from __future__ import annotations

import pytest
import yaml

from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.harnesses_doc import render_harnesses_doc, write_harnesses_doc
from murder.llm.harnesses.model_cache import clear_model_cache, set_discovered_models
from murder.state.storage.paths import harnesses_and_models_md


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_model_cache()
    yield
    clear_model_cache()


def test_render_lists_harness_names_and_models():
    models = {"claude_code": [("sonnet", "Claude Sonnet"), ("opus", "opus")]}
    doc = render_harnesses_doc(["claude_code"], models)
    assert "## claude_code" in doc
    assert "`sonnet` — Claude Sonnet" in doc
    # label == id collapses to just the id (no redundant em-dash)
    assert "`opus`" in doc
    assert "opus — opus" not in doc


def test_render_derives_effort_from_classvars():
    doc = render_harnesses_doc(["claude_code", "codex", "cursor"], {})
    cc_efforts = ", ".join(REGISTRY["claude_code"].supported_efforts)
    codex_efforts = ", ".join(REGISTRY["codex"].supported_efforts)
    cursor_efforts = ", ".join(REGISTRY["cursor"].supported_efforts)
    assert f"Effort levels: {cc_efforts}" in doc
    assert f"Effort levels: {codex_efforts}" in doc
    assert f"Effort levels: {cursor_efforts}" in doc


def test_render_no_effort_harness_shows_none():
    # native_coding_crow declares no supported_efforts
    doc = render_harnesses_doc(["native_coding_crow"], {})
    assert "## native_coding_crow" in doc
    assert "Effort levels: (none)" in doc


def test_render_empty_models_listed_not_omitted():
    doc = render_harnesses_doc(["pi"], {"pi": []})
    assert "## pi" in doc
    assert "(no models discovered)" in doc


def test_render_unknown_harness_in_models_is_safe():
    # a harness with no adapter should not crash effort derivation
    doc = render_harnesses_doc(["not_a_harness"], {})
    assert "## not_a_harness" in doc
    assert "Effort levels: (none)" in doc


def test_render_trailing_newline_stable():
    doc = render_harnesses_doc(["pi"], {})
    assert doc.endswith("\n")
    assert not doc.endswith("\n\n")


def _write_roles(tmp_path, harnesses):
    murder_dir = tmp_path / ".murder"
    murder_dir.mkdir(exist_ok=True)
    (murder_dir / "roles.yaml").write_text(
        yaml.safe_dump({"default_crow": {"harness": harnesses[0], "harnesses": harnesses}}),
        encoding="utf-8",
    )


def test_write_doc_uses_cache_and_writes_file(tmp_path):
    _write_roles(tmp_path, ["claude_code", "codex"])
    set_discovered_models("claude_code", [("sonnet", "Claude Sonnet 4")])
    write_harnesses_doc(tmp_path)
    path = harnesses_and_models_md(tmp_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "## claude_code" in text
    assert "Claude Sonnet 4" in text
    assert "## codex" in text


def test_write_doc_omits_disabled_harness(tmp_path):
    # only claude_code enabled -> codex (with a non-empty classvar fallback)
    # must NOT appear, so the planner can't assign a disabled harness.
    _write_roles(tmp_path, ["claude_code"])
    write_harnesses_doc(tmp_path)
    text = harnesses_and_models_md(tmp_path).read_text(encoding="utf-8")
    assert "## claude_code" in text
    assert "## codex" not in text


def test_write_doc_creates_parent_dir(tmp_path):
    # repo with no .murder/ dir yet -> config falls back to defaults
    write_harnesses_doc(tmp_path)
    assert harnesses_and_models_md(tmp_path).exists()


def test_write_doc_empty_cache_falls_back(tmp_path):
    # no discovered models -> accessor falls back to classvars; still writes
    write_harnesses_doc(tmp_path)
    text = harnesses_and_models_md(tmp_path).read_text(encoding="utf-8")
    assert "# Harnesses and models" in text
