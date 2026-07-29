"""Unit tests for ACP/app-server model option resolution."""

from __future__ import annotations

import pytest

from murder.llm.harness_control.acp.bootstrap import _argv_with_startup_model
from murder.llm.harness_control.adapters.rpc_model_options import (
    effort_from_config_options,
    fast_enabled_from_config_options,
    plan_acp_model_config_writes,
    resolve_acp_model_option_value,
    same_model_id,
)

_VARIANT_CATALOG = [
    {
        "id": "model",
        "category": "model",
        "type": "select",
        "currentValue": "grok-4.5[effort=high,fast=true]",
        "options": [
            {"value": "default[]", "name": "Auto"},
            {"value": "composer-2.5[fast=true]", "name": "composer-2.5"},
            {"value": "composer-2.5[fast=false]", "name": "composer-2.5"},
            {"value": "grok-4.5[effort=high,fast=true]", "name": "grok-4.5"},
            {"value": "gpt-5.5[context=272k,reasoning=medium,fast=false]", "name": "gpt-5.5"},
        ],
    }
]

_PARAMETERIZED_CATALOG = [
    {
        "id": "model",
        "category": "model",
        "type": "select",
        "currentValue": "composer-2.5",
        "options": [
            {"value": "default", "name": "Auto"},
            {"value": "composer-2.5", "name": "Composer 2.5"},
            {"value": "gpt-5.5", "name": "GPT-5.5"},
            {"value": "grok-4.5", "name": "Cursor Grok 4.5"},
        ],
    },
    {
        "id": "fast",
        "category": "model_config",
        "type": "select",
        "currentValue": "true",
        "options": [
            {"value": "false", "name": "Off"},
            {"value": "true", "name": "Fast"},
        ],
    },
]


def test_same_model_id_strips_bracket_params_and_aliases_auto() -> None:
    assert same_model_id("composer-2.5", "composer-2.5[fast=true]")
    assert same_model_id("auto", "default[]")
    assert same_model_id("auto", "default")
    assert not same_model_id("composer-2.5", "gpt-5.5")


def test_resolve_prefers_name_and_fast_flag() -> None:
    assert (
        resolve_acp_model_option_value("composer-2.5", _VARIANT_CATALOG, fast_enabled=True)
        == "composer-2.5[fast=true]"
    )
    assert (
        resolve_acp_model_option_value("composer-2.5", _VARIANT_CATALOG, fast_enabled=False)
        == "composer-2.5[fast=false]"
    )
    assert resolve_acp_model_option_value("auto", _VARIANT_CATALOG) == "default[]"
    assert (
        resolve_acp_model_option_value("gpt-5.5", _VARIANT_CATALOG)
        == "gpt-5.5[context=272k,reasoning=medium,fast=false]"
    )


def test_resolve_defaults_to_non_fast_when_unspecified() -> None:
    assert (
        resolve_acp_model_option_value("composer-2.5", _VARIANT_CATALOG)
        == "composer-2.5[fast=false]"
    )


def test_plan_parameterized_writes_include_fast_off_for_slow() -> None:
    writes = plan_acp_model_config_writes(
        _PARAMETERIZED_CATALOG,
        model_id="composer-2.5",
        effort="slow",
    )
    assert writes == [("fast", "false")]


def test_plan_parameterized_model_change_speculates_fast() -> None:
    catalog = [
        {
            "id": "model",
            "category": "model",
            "type": "select",
            "currentValue": "default",
            "options": [
                {"value": "default", "name": "Auto"},
                {"value": "composer-2.5", "name": "Composer 2.5"},
            ],
        }
    ]
    writes = plan_acp_model_config_writes(
        catalog,
        model_id="composer-2.5",
        fast_enabled=False,
    )
    assert writes == [("model", "composer-2.5"), ("fast", "false")]


def test_fast_and_effort_readback_from_parameterized_catalog() -> None:
    assert fast_enabled_from_config_options(_PARAMETERIZED_CATALOG) is True
    assert effort_from_config_options(_PARAMETERIZED_CATALOG) == "fast"
    slow = [
        dict(_PARAMETERIZED_CATALOG[0]),
        {**_PARAMETERIZED_CATALOG[1], "currentValue": "false"},
    ]
    assert fast_enabled_from_config_options(slow) is False
    assert effort_from_config_options(slow) == "slow"


def test_resolve_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="not in the ACP configOptions catalog"):
        resolve_acp_model_option_value("definitely-missing", _VARIANT_CATALOG)


def test_argv_inserts_model_before_acp_subcommand() -> None:
    assert _argv_with_startup_model(("agent", "acp"), "composer-2.5") == (
        "agent",
        "--model",
        "composer-2.5",
        "acp",
    )
    assert _argv_with_startup_model(("agent", "--model", "auto", "acp"), "composer-2.5") == (
        "agent",
        "--model",
        "auto",
        "acp",
    )
    assert _argv_with_startup_model(("agent", "acp"), None) == ("agent", "acp")
