"""Lexical hit lives inside a large function — shaping should keep that function."""


def unrelated_top() -> str:
    return "nope"


def process_payload(data: dict) -> dict:
    """Large-ish body; the distinctive token lives here."""
    # setup
    acc = {}
    for key, value in data.items():
        acc[key] = value
    # distinctive lexical target for ranking tests:
    magic_validation_token = acc.get("name", "")
    if not magic_validation_token:
        raise ValueError("missing")
    # more body so the unit is clearly the right shape target
    acc["ok"] = True
    acc["len"] = len(magic_validation_token)
    acc["upper"] = magic_validation_token.upper()
    return acc


def another_unrelated() -> int:
    return 42
