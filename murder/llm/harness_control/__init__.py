"""Verified, evidence-driven control of interactive terminal harnesses.

This package deliberately sits beside the legacy harness adapters while their
callers are migrated.  Its public boundary is semantic: adapters parse and
lower; controllers reconcile observed state and decide actions.
"""

from murder.llm.harness_control.model import *  # noqa: F403
