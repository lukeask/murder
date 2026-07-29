"""Resolve Murder model targets against live RPC option catalogs.

TUI adapters match ``SelectModel`` against parser-observed picker rows. ACP and
Codex app-server do the same against ``configOptions`` / ``model/list`` rows:
short catalog ids such as ``composer-2.5`` or ``auto`` must map onto the
agent's full option values (``composer-2.5[fast=true]``, ``default[]``).

When Cursor ACP clients advertise ``_meta.parameterizedModelPicker``, the
catalog splits into a base ``model`` select plus separate ``model_config`` /
``thought_level`` options (``fast``, ``effort``, ``reasoning``). Murder's
slow/fast startup setting maps onto the ``fast`` option in that mode.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_BRACKET_RE = re.compile(r"\[.*\]\s*$")
_FAST_TRUE_RE = re.compile(r"(?:^|[,\[\s])fast\s*=\s*true(?:[,\]\s]|$)", re.IGNORECASE)
_FAST_FALSE_RE = re.compile(r"(?:^|[,\[\s])fast\s*=\s*false(?:[,\]\s]|$)", re.IGNORECASE)

# Murder/Cursor aliases that collapse to the ACP ``default`` / Auto row.
_AUTO_IDS = frozenset({"auto", "default"})


def model_base_id(value: str) -> str:
    """Strip ACP-style ``[param=...]`` suffixes before comparing model ids."""
    return _BRACKET_RE.sub("", value).strip()


def canonical_model_id(value: str) -> str:
    base = model_base_id(value).casefold().strip()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")


def model_identity_tokens(value: str) -> frozenset[str]:
    """Identity set used for fuzzy model matching (aliases + base id)."""
    canon = canonical_model_id(value)
    tokens = {canon}
    if canon in _AUTO_IDS:
        tokens.update(_AUTO_IDS)
    return frozenset(tokens)


def same_model_id(left: str, right: str) -> bool:
    if left == right:
        return True
    return bool(model_identity_tokens(left) & model_identity_tokens(right))


def _find_option(
    config_options: Sequence[Mapping[str, Any]], option_id: str
) -> Mapping[str, Any] | None:
    for option in config_options:
        oid = option.get("id") or option.get("name")
        if oid == option_id:
            return option
    return None


def _option_current(option: Mapping[str, Any]) -> str | None:
    current = option.get("currentValue") or option.get("value")
    if isinstance(current, str) and current.strip():
        return current.strip()
    if isinstance(current, bool):
        return "true" if current else "false"
    return None


def _option_choice_values(option: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    choices = option.get("options")
    if not isinstance(choices, list):
        return rows
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        value = choice.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        name = choice.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else value
        rows.append((value, label))
    return rows


def _option_rows(config_options: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Return ``(value, name)`` pairs for the model selector option."""
    option = _find_option(config_options, "model")
    if option is None:
        for candidate in config_options:
            if candidate.get("category") == "model" and (
                candidate.get("id") in {None, "model", "modelId"}
                or candidate.get("name") in {"model", "modelId"}
            ):
                option = candidate
                break
    if option is None:
        return []
    # Only the primary model select — never explode model_config rows.
    if option.get("category") not in {None, "model"} and option.get("id") not in {
        "model",
        "modelId",
    }:
        return []
    return _option_choice_values(option)


def _score_fast(value: str, *, fast_enabled: bool | None) -> int:
    if fast_enabled is None:
        # Murder's Cursor default is slow; prefer non-fast when unspecified.
        return 1 if not _value_is_fast(value) else 0
    if fast_enabled:
        return 2 if _value_is_fast(value) else 0
    return 2 if not _value_is_fast(value) else 0


def _value_is_fast(value: str) -> bool:
    lowered = value.casefold()
    if _FAST_TRUE_RE.search(value) or lowered.endswith("-fast") or "[fast]" in lowered:
        return True
    if _FAST_FALSE_RE.search(value):
        return False
    return False


def _parse_fast_flag(value: str) -> bool | None:
    lowered = value.casefold().strip()
    if lowered in {"true", "1", "on", "fast"} or _FAST_TRUE_RE.search(value):
        return True
    if (
        lowered in {"false", "0", "off", "slow"}
        or _FAST_FALSE_RE.search(value)
    ):
        return False
    if lowered.endswith("-fast"):
        return True
    return None


def fast_enabled_from_config_options(
    config_options: Sequence[Mapping[str, Any]],
) -> bool | None:
    """Read the live ``fast`` model_config option, or parse exploded model values."""
    fast_option = _find_option(config_options, "fast")
    if fast_option is not None:
        current = _option_current(fast_option)
        return _parse_fast_flag(current) if current is not None else None
    model_id = model_id_from_config_options(config_options)
    return _parse_fast_flag(model_id) if model_id is not None else None


def _score_effort(value: str, *, effort: str | None) -> int:
    if effort is None:
        return 0
    needle = f"effort={effort.casefold()}"
    reasoning = f"reasoning={effort.casefold()}"
    lowered = value.casefold()
    if needle in lowered or reasoning in lowered:
        return 2
    return 0


def resolve_acp_model_option_value(
    target_model_id: str,
    config_options: Sequence[Mapping[str, Any]],
    *,
    fast_enabled: bool | None = None,
    effort: str | None = None,
) -> str:
    """Map a Murder model id onto an ACP ``configOptions`` select value.

    Prefers exact value matches, then option ``name`` / base-id matches the way
    pane parsers derive short ids from labels. When several parameterized values
    share a base id, prefer ones matching ``fast_enabled`` / ``effort``.
    """
    target = target_model_id.strip()
    if not target:
        raise ValueError("model target must be non-empty")
    rows = _option_rows(config_options)
    if not rows:
        # No live catalog yet: pass the Murder id through (argv --model / older agents).
        return target

    exact = [value for value, _name in rows if value == target]
    if exact:
        return exact[0]

    candidates = [
        value
        for value, name in rows
        if same_model_id(value, target) or same_model_id(name, target)
    ]
    if not candidates:
        raise ValueError(f"requested model {target!r} is not in the ACP configOptions catalog")

    candidates.sort(
        key=lambda value: (
            _score_fast(value, fast_enabled=fast_enabled)
            + _score_effort(value, effort=effort),
            -len(value),
        ),
        reverse=True,
    )
    return candidates[0]


def model_id_from_config_options(config_options: Sequence[Mapping[str, Any]]) -> str | None:
    """Read the active model id from a full ``configOptions`` list."""
    option = _find_option(config_options, "model")
    if option is None:
        for candidate in config_options:
            option_id = candidate.get("id") or candidate.get("name")
            category = candidate.get("category")
            if option_id not in {"model", "modelId"} and category != "model":
                continue
            option = candidate
            break
    if option is None:
        return None
    return _option_current(option)


def _effort_aliases(effort: str) -> frozenset[str]:
    canon = effort.casefold().strip()
    aliases = {canon, canon.replace("_", "-"), canon.replace("-", "")}
    if canon in {"xhigh", "x-high", "extra-high", "extrahigh"}:
        aliases.update({"xhigh", "x-high", "extra-high", "extrahigh"})
    if canon in {"max", "maximum"}:
        aliases.update({"max", "maximum"})
    return frozenset(aliases)


def resolve_acp_thought_option(
    config_options: Sequence[Mapping[str, Any]],
    effort: str,
) -> tuple[str, str] | None:
    """Map a Murder effort id onto ``(configId, value)`` for effort/reasoning."""
    target = effort.strip()
    if not target or target in {"slow", "fast"}:
        return None
    aliases = _effort_aliases(target)
    for option_id in ("effort", "reasoning"):
        option = _find_option(config_options, option_id)
        if option is None:
            continue
        for value, name in _option_choice_values(option):
            if value.casefold() in aliases or canonical_model_id(name) in {
                canonical_model_id(alias) for alias in aliases
            }:
                return option_id, value
    return None


def effort_from_config_options(config_options: Sequence[Mapping[str, Any]]) -> str | None:
    """Read active effort/reasoning, or Cursor slow/fast from the ``fast`` toggle."""
    for option_id in ("effort", "reasoning"):
        option = _find_option(config_options, option_id)
        if option is None:
            continue
        current = _option_current(option)
        if current is not None:
            return current
    fast = fast_enabled_from_config_options(config_options)
    if fast is True:
        return "fast"
    if fast is False:
        return "slow"
    model_id = model_id_from_config_options(config_options)
    if model_id is None:
        return None
    match = re.search(r"(?:effort|reasoning)=([a-z0-9-]+)", model_id, re.IGNORECASE)
    if match:
        return match.group(1).casefold()
    return None


def _catalog_looks_parameterized(config_options: Sequence[Mapping[str, Any]]) -> bool:
    """True when model choices are bare ids (no ``[fast=…]`` exploded variants)."""
    if _find_option(config_options, "fast") is not None:
        return True
    rows = _option_rows(config_options)
    if not rows:
        return False
    return all("[" not in value for value, _name in rows)


def plan_acp_model_config_writes(
    config_options: Sequence[Mapping[str, Any]],
    *,
    model_id: str | None = None,
    fast_enabled: bool | None = None,
    effort: str | None = None,
) -> list[tuple[str, str]]:
    """Return ordered ``(configId, value)`` writes to realize a Murder model target.

    Includes a model write when ``model_id`` is set, then ``fast`` / thought-level
    writes when those options exist (or will exist after the model write for
    non-Auto targets). Callers should re-plan against the post-model catalog
    when applying follow-up writes after a model change.
    """
    writes: list[tuple[str, str]] = []
    catalog = list(config_options)
    effective_fast = fast_enabled
    effective_effort = effort
    if effective_effort in {"slow", "fast"} and effective_fast is None:
        effective_fast = effective_effort == "fast"
        effective_effort = None

    if model_id is not None and model_id.strip():
        value = resolve_acp_model_option_value(
            model_id,
            catalog,
            fast_enabled=effective_fast,
            effort=effective_effort,
        )
        current = model_id_from_config_options(catalog)
        if current != value:
            writes.append(("model", value))

    if effective_fast is not None:
        want = "true" if effective_fast else "false"
        fast_option = _find_option(catalog, "fast")
        if fast_option is not None:
            if _option_current(fast_option) != want:
                writes.append(("fast", want))
        elif (
            model_id is not None
            and not same_model_id(model_id, "auto")
            and _catalog_looks_parameterized(catalog)
        ):
            # Parameterized catalogs expose ``fast`` only after the model row is
            # selected; stage the write so callers can apply it on the post-model
            # catalog. Variants-mode agents (exploded ``model[fast=…]`` values)
            # have no separate ``fast`` option — never speculate there.
            writes.append(("fast", want))

    if effective_effort is not None and effective_effort.strip():
        thought = resolve_acp_thought_option(catalog, effective_effort)
        if thought is not None:
            option_id, value = thought
            option = _find_option(catalog, option_id)
            if option is None or _option_current(option) != value:
                writes.append((option_id, value))

    return writes


__all__ = [
    "canonical_model_id",
    "effort_from_config_options",
    "fast_enabled_from_config_options",
    "model_base_id",
    "model_id_from_config_options",
    "model_identity_tokens",
    "plan_acp_model_config_writes",
    "resolve_acp_model_option_value",
    "resolve_acp_thought_option",
    "same_model_id",
]
