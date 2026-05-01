"""
Tmux-backed agent tools (function-call schemas + dispatch). Lives in-repo; no PyPI package.

Import with the **repository root** on ``sys.path`` (``tools`` is one level below root)::

    from pathlib import Path
    import sys
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from tools.tmux_llm import OPENAI_TOOL_DEFINITIONS, dispatch_tool

**Exit code in footers** is taken from the last visible ``__TMUX_LL_EXIT__`` line in the
captured scrollback; if that line was scrolled away, an idle pane may show ``[idle | bash]``
without ``exit N``.
"""

from .api import (
    OPENAI_TOOL_DEFINITIONS,
    anthropic_input_schemas,
    dispatch_tool,
    dispatch_tool_call_json,
)
from .backend import SESSION_ENV

__all__ = [
    "OPENAI_TOOL_DEFINITIONS",
    "SESSION_ENV",
    "anthropic_input_schemas",
    "dispatch_tool",
    "dispatch_tool_call_json",
]
