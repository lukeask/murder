from __future__ import annotations

from murder.cli import _validate_configured_harness_binaries
from murder.config import Config


def _config(
    *,
    collaborator_harness: str = "claude_code",
    collaborator_binary: str | None = None,
    default_monkey_harness: str = "cursor",
    default_monkey_harnesses: list[str] | None = None,
    default_monkey_binary: str | None = None,
) -> Config:
    return Config.model_validate(
        {
            "project": {"name": "test", "repo_path": "."},
            "collaborator": {
                "kind": "harness",
                "harness": collaborator_harness,
                "binary": collaborator_binary,
            },
            "sentinel": {
                "kind": "api",
                "provider": "openrouter",
                "model": "sentinel-model",
            },
            "augur": {
                "kind": "api",
                "provider": "openrouter",
                "model": "augur-model",
            },
            "default_monkey": {
                "kind": "harness",
                "harness": default_monkey_harness,
                "harnesses": default_monkey_harnesses,
                "binary": default_monkey_binary,
            },
        }
    )


def test_doctor_validates_only_configured_harness_binaries(monkeypatch) -> None:
    cfg = _config(collaborator_harness="pi", default_monkey_harness="pi")

    monkeypatch.setattr(
        "murder.cli.shutil.which",
        lambda exe: f"/bin/{exe}" if exe == "pi" else None,
    )

    assert _validate_configured_harness_binaries(cfg) == []


def test_doctor_validates_default_monkey_harness_pool(monkeypatch) -> None:
    cfg = _config(default_monkey_harnesses=["cursor", "codex", "pi"])
    present = {"agent", "claude", "pi"}

    monkeypatch.setattr(
        "murder.cli.shutil.which",
        lambda exe: f"/bin/{exe}" if exe in present else None,
    )

    assert _validate_configured_harness_binaries(cfg) == [
        "default_monkey harness codex: codex not on PATH"
    ]


def test_doctor_uses_configured_binary_for_primary_harness(monkeypatch) -> None:
    cfg = _config(default_monkey_harness="cursor", default_monkey_binary="cursor-agent")
    present = {"claude", "cursor-agent"}

    monkeypatch.setattr(
        "murder.cli.shutil.which",
        lambda exe: f"/bin/{exe}" if exe in present else None,
    )

    assert _validate_configured_harness_binaries(cfg) == []
