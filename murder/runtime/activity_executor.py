"""Production activity executors bound to session controllers.

Routing and admission remain pure decisions. This module owns the side-effect
boundary for claimed activities: send structured turns through the owning
session controller when a compatible live session exists.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from uuid import uuid4

from murder.runtime.sessions.contracts import (
    PrincipalKind,
    PrincipalRef,
    SendStructuredMessage,
)
from murder.runtime.sessions.registry import registry_for_connection
from murder.work.activities.runtime import (
    ActivityClaim,
    ActivityFailure,
    ActivityOutcome,
    ActivityRecord,
    ActivitySuccess,
)
from murder.work.workflows.runtime import RunAgentTurnActivity, RunReviewActivity


def build_session_bound_executor(
    connection: sqlite3.Connection,
) -> Callable[
    [ActivityRecord, ActivityClaim, Callable[[], ActivityClaim]],
    object,
]:
    """Return an async executor that drives agent turns via SessionController."""

    async def execute(
        activity: ActivityRecord,
        claim: ActivityClaim,
        renew: Callable[[], ActivityClaim],
    ) -> ActivityOutcome:
        del claim  # ownership already verified by the dispatcher claim fence
        renew()
        payload = activity.payload
        if not isinstance(payload, (RunAgentTurnActivity, RunReviewActivity)):
            return ActivityFailure(
                code="unsupported_activity",
                message=f"no production executor for {type(payload).__name__}",
                retryable=False,
            )
        route = activity.route
        if route is None:
            return ActivityFailure(
                code="not_routed",
                message="activity has no persisted execution route",
                retryable=False,
            )
        session_id = route.selected_session_id or activity.session_id
        if session_id is None:
            return ActivityFailure(
                code="session_required",
                message=(
                    "no compatible live session for this activity; "
                    "create or reuse a harness session before retrying"
                ),
                retryable=True,
            )
        registry = registry_for_connection(connection)
        controller = await registry.get_or_create(session_id, recover=True)
        instructions = (
            payload.instructions
            if isinstance(payload, RunAgentTurnActivity)
            else f"Review {payload.subject_ref}: {payload.instructions}"
        )
        renew()
        receipt = await controller.execute(
            SendStructuredMessage(
                operation_id=uuid4(),
                text=instructions,
                activity_id=activity.activity_id,
            ),
            principal=PrincipalRef(
                kind=PrincipalKind.WORKFLOW,
                id=str(activity.workflow_id),
            ),
        )
        renew()
        return ActivitySuccess(
            output={
                "session_id": str(session_id),
                "operation_id": str(receipt.operation_id),
                "activity_kind": payload.type,
            }
        )

    return execute  # type: ignore[return-value]


__all__ = ["build_session_bound_executor"]
