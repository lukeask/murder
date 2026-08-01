"""Frontend framework extractors and enrichers for Context Assembler 2.

React and Angular are post-pass enrichers over JS/TS base extraction. Vue and
Svelte ship dedicated SFC language extractors (``.vue`` / ``.svelte``) because
the JS/TS grammars cannot parse single-file components.
"""

from __future__ import annotations

from murder.context_compiler.extraction.frameworks.angular import (
    AngularEnricher,
    register_angular_enricher,
)
from murder.context_compiler.extraction.frameworks.react import (
    ReactEnricher,
    register_react_enricher,
)
from murder.context_compiler.extraction.frameworks.svelte import (
    SvelteExtractor,
    register_svelte_extractor,
)
from murder.context_compiler.extraction.frameworks.vue import (
    VueExtractor,
    register_vue_extractor,
)
from murder.context_compiler.extraction.registry import ExtractorRegistry


def register_framework_adapters(registry: ExtractorRegistry) -> None:
    """Register Vue/Svelte extractors and React/Angular enrichers."""
    register_vue_extractor(registry)
    register_svelte_extractor(registry)
    register_react_enricher(registry)
    register_angular_enricher(registry)


__all__ = [
    "AngularEnricher",
    "ReactEnricher",
    "SvelteExtractor",
    "VueExtractor",
    "register_angular_enricher",
    "register_framework_adapters",
    "register_react_enricher",
    "register_svelte_extractor",
    "register_vue_extractor",
]
