from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.domain.entities import ApprovalRequest
from app.domain.enums import ApprovalStatus, AuditAction, SourceType


class ApprovalRequestRepository(ABC):
    """Persistence port for approval requests and their audit/outbox trail.

    `domain/` depends on this abstract interface only; `app/db/repository.py` is the
    concrete SQLAlchemy adapter. That keeps the business rules here unit-testable
    against an in-memory fake, without a database.
    """

    @abstractmethod
    async def create(
        self,
        *,
        request_id: str,
        workspace_id: str,
        created_by_user_id: str,
        source_type: SourceType,
        source_id: str,
        title: str,
        description: str | None,
        reviewer_user_ids: Sequence[str],
    ) -> ApprovalRequest: ...

    @abstractmethod
    async def get(self, *, workspace_id: str, request_id: str) -> ApprovalRequest | None: ...

    @abstractmethod
    async def transition(
        self,
        *,
        workspace_id: str,
        request_id: str,
        new_status: ApprovalStatus,
        decided_by_user_id: str,
        decision_comment: str | None,
        decision_reason: str | None,
    ) -> ApprovalRequest:
        """Atomically move a *pending* request to a final state.

        Must be implemented as a single conditional UPDATE (`WHERE status = 'pending'`)
        so two concurrent decisions on the same request can't both succeed. Raises
        `ApprovalRequestNotFoundError` if the request doesn't exist in this workspace,
        or `InvalidTransitionError` if it's already in a final state.
        """
        ...

    @abstractmethod
    async def list(
        self,
        *,
        workspace_id: str,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ApprovalRequest], int]: ...

    @abstractmethod
    async def record_audit_entry(
        self,
        *,
        workspace_id: str,
        approval_request_id: str,
        actor_user_id: str,
        action: AuditAction,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    async def record_outbox_event(
        self,
        *,
        workspace_id: str,
        approval_request_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...
