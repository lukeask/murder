"""default_crow harness/model pools and resolution."""

from __future__ import annotations

from murder.config import (
    HarnessRoleConfig,
    resolve_default_crow_harness,
    resolve_default_crow_startup_model,
    stable_bucket_index,
)


def test_stable_bucket_index_deterministic() -> None:
    modulo = 3
    assert stable_bucket_index("t001", modulo) == stable_bucket_index("t001", modulo)
    assert 0 <= stable_bucket_index("t001", modulo) < modulo


def test_resolve_harness_pool_spreads_tickets() -> None:
    cfg = HarnessRoleConfig(
        harness="cursor",
        harnesses=["cursor", "codex", "claude_code"],
    )
    a = resolve_default_crow_harness(cfg, {"id": "t001"})
    b = resolve_default_crow_harness(cfg, {"id": "t002"})
    assert a in cfg.harnesses
    assert b in cfg.harnesses


def test_resolve_harness_ticket_override() -> None:
    cfg = HarnessRoleConfig(harness="cursor", harnesses=["cursor", "codex"])
    assert resolve_default_crow_harness(cfg, {"id": "t001", "harness": "pi"}) == "pi"


def test_resolve_model_pool_and_override() -> None:
    cfg = HarnessRoleConfig(
        harness="cursor",
        startup_model="a",
        startup_models=["m1", "m2", "m3"],
    )
    assert resolve_default_crow_startup_model(cfg, {"id": "t007"}) in ("m1", "m2", "m3")
    assert resolve_default_crow_startup_model(cfg, {"id": "t007", "model": "z9"}) == "z9"


def test_resolve_model_single_startup_model() -> None:
    cfg = HarnessRoleConfig(harness="cursor", startup_model="solo", startup_models=None)
    assert resolve_default_crow_startup_model(cfg, {"id": "t1"}) == "solo"


def test_resolve_model_pool_by_harness() -> None:
    cfg = HarnessRoleConfig(
        harness="cursor",
        harnesses=["cursor", "codex"],
        startup_model="composer",
        startup_models_by_harness={
            "cursor": ["composer"],
            "codex": ["gpt-5.5", "gpt-5.4"],
        },
    )
    assert resolve_default_crow_startup_model(cfg, {"id": "t007"}, "cursor") == "composer"
    assert resolve_default_crow_startup_model(cfg, {"id": "t007"}, "codex") in (
        "gpt-5.5",
        "gpt-5.4",
    )


def test_resolve_model_pool_without_ticket_uses_first_model() -> None:
    cfg = HarnessRoleConfig(
        harness="codex",
        startup_models=["gpt-5.4", "gpt-5.5"],
    )

    assert resolve_default_crow_startup_model(cfg, None) == "gpt-5.4"


def test_empty_models_by_harness_normalized() -> None:
    cfg = HarnessRoleConfig.model_validate(
        {
            "kind": "harness",
            "harness": "cursor",
            "startup_models_by_harness": {"cursor": [" "], "codex": ["gpt-5.5"]},
        }
    )
    assert cfg.startup_models_by_harness == {"codex": ["gpt-5.5"]}


def test_startup_models_all_blank_becomes_none() -> None:
    cfg = HarnessRoleConfig.model_validate(
        {
            "kind": "harness",
            "harness": "cursor",
            "startup_models": ["  ", ""],
        }
    )
    assert cfg.startup_models is None


def test_empty_harnesses_list_normalized() -> None:
    cfg = HarnessRoleConfig.model_validate(
        {"kind": "harness", "harness": "cursor", "harnesses": []},
    )
    assert cfg.harnesses is None
