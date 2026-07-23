"""The committed TypeScript application protocol is generated, never hand-ported."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "generate_application_protocol.py"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_application_protocol", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_typescript_is_current() -> None:
    generator = _generator()
    output = ROOT / "inktui" / "src" / "generated" / "applicationProtocol.ts"
    assert output.read_text(encoding="utf-8") == generator.render()


def test_generated_contract_names_public_capabilities_not_bus_primitives() -> None:
    generated = (ROOT / "inktui" / "src" / "generated" / "applicationProtocol.ts").read_text(
        encoding="utf-8"
    )
    assert "agent.message" in generated
    assert "orchestration.execute" not in generated
    assert "terminal.attach" in generated
    assert "subscription.event" in generated
    assert "target_worker" not in generated
    assert "EventFilter" not in generated
    assert "RpcMessage" not in generated
    assert "PubMessage" not in generated
    assert "export type QueryResultMap" in generated
    assert 'readonly "roster.get": { readonly params: {  }; readonly result:' in generated
    assert ": any" not in generated
