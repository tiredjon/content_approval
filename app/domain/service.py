import logging
from collections.abc import Sequence
from typing import Any

from app.domain.entities import ApprovalRequest
from app.domain.enums import ApprovalStatus, AuditAction, OutboxEventType, SourceType
from app.domain.exceptions import ApprovalRequestNotFoundError, NotAuthorizedForDecisionError
from app.domain.ids import generate_id
from app.domain.repository import ApprovalRequestRepository

logger = logging.getLogger(__name__)


class ApprovalService:
    """Business logic for approval requests.

    Takes only the repository port and plain scalar arguments (never a `Principal` or
    an API schema) so it stays decoupled from both the auth stub and the HTTP layer —
    see CLAUDE.md's layering conventions.
    """

    def __init__(self, repository: ApprovalRequestRepository) -> None:
        self._repository = repository

    async def create_request(
        self,
        *,
        workspace_id: str,
        created_by_user_id: str,
        source_type: SourceType,
        source_id: str,
        title: str,
        description: str | None,
        reviewer_user_ids: Sequence[str],
    ) -> ApprovalRequest:
        request_id = generate_id("ar")
        request = await self._repository.create(
            request_id=request_id,
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            source_type=source_type,
            source_id=source_id,
            title=title,
            description=description,
            reviewer_user_ids=reviewer_user_ids,
        )
        await self._repository.record_audit_entry(
            workspace_id=workspace_id,
            approval_request_id=request_id,
            actor_user_id=created_by_user_id,
            action=AuditAction.CREATED,
            details={"title": title, "source_type": source_type.value, "source_id": source_id},
        )
        await self._repository.record_outbox_event(
            workspace_id=workspace_id,
            approval_request_id=request_id,
            event_type=OutboxEventType.APPROVAL_REQUEST_CREATED.value,
            payload={
                "id": request_id,
                "workspace_id": workspace_id,
                "source_type": source_type.value,
                "source_id": source_id,
                "status": request.status.value,
                "reviewer_user_ids": list(reviewer_user_ids),
            },
        )
        logger.info(
            "approval request created",
            extra={
                "context": {
                    "workspace_id": workspace_id,
                    "request_id": request_id,
                    "actor_user_id": created_by_user_id,
                }
            },
        )
        return request

    async def get_request(self, *, workspace_id: str, request_id: str) -> ApprovalRequest:
        request = await self._repository.get(workspace_id=workspace_id, request_id=request_id)
        if request is None:
            raise ApprovalRequestNotFoundError(request_id)
        return request

    async def list_requests(
        self,
        *,
        workspace_id: str,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ApprovalRequest], int]:
        return await self._repository.list(
            workspace_id=workspace_id, status=status, limit=limit, offset=offset
        )

    async def approve_request(
        self,
        *,
        workspace_id: str,
        request_id: str,
        actor_user_id: str,
        comment: str | None,
    ) -> ApprovalRequest:
        request = await self._get_or_raise(workspace_id=workspace_id, request_id=request_id)
        self._require_reviewer(request, actor_user_id)
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            new_status=ApprovalStatus.APPROVED,
            actor_user_id=actor_user_id,
            comment=comment,
            reason=None,
            audit_action=AuditAction.APPROVED,
            event_type=OutboxEventType.APPROVAL_REQUEST_APPROVED,
        )

    async def reject_request(
        self,
        *,
        workspace_id: str,
        request_id: str,
        actor_user_id: str,
        reason: str,
    ) -> ApprovalRequest:
        request = await self._get_or_raise(workspace_id=workspace_id, request_id=request_id)
        self._require_reviewer(request, actor_user_id)
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            new_status=ApprovalStatus.REJECTED,
            actor_user_id=actor_user_id,
            comment=None,
            reason=reason,
            audit_action=AuditAction.REJECTED,
            event_type=OutboxEventType.APPROVAL_REQUEST_REJECTED,
        )

    async def cancel_request(
        self,
        *,
        workspace_id: str,
        request_id: str,
        actor_user_id: str,
        reason: str,
    ) -> ApprovalRequest:
        request = await self._get_or_raise(workspace_id=workspace_id, request_id=request_id)
        if actor_user_id != request.created_by_user_id:
            raise NotAuthorizedForDecisionError(
                request_id=request_id,
                actor_user_id=actor_user_id,
                reason="only the request's creator can cancel it",
            )
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            new_status=ApprovalStatus.CANCELLED,
            actor_user_id=actor_user_id,
            comment=None,
            reason=reason,
            audit_action=AuditAction.CANCELLED,
            event_type=OutboxEventType.APPROVAL_REQUEST_CANCELLED,
        )

    async def _get_or_raise(self, *, workspace_id: str, request_id: str) -> ApprovalRequest:
        request = await self._repository.get(workspace_id=workspace_id, request_id=request_id)
        if request is None:
            raise ApprovalRequestNotFoundError(request_id)
        return request

    def _require_reviewer(self, request: ApprovalRequest, actor_user_id: str) -> None:
        # An empty reviewer list means "anyone with approval:decide may decide" — see
        # CLAUDE.md's "Authorization nuance beyond the coarse action list".
        if request.reviewer_user_ids and actor_user_id not in request.reviewer_user_ids:
            raise NotAuthorizedForDecisionError(
                request_id=request.id,
                actor_user_id=actor_user_id,
                reason="user is not a listed reviewer for this request",
            )

    async def _decide(
        self,
        *,
        workspace_id: str,
        request_id: str,
        new_status: ApprovalStatus,
        actor_user_id: str,
        comment: str | None,
        reason: str | None,
        audit_action: AuditAction,
        event_type: OutboxEventType,
    ) -> ApprovalRequest:
        updated = await self._repository.transition(
            workspace_id=workspace_id,
            request_id=request_id,
            new_status=new_status,
            decided_by_user_id=actor_user_id,
            decision_comment=comment,
            decision_reason=reason,
        )
        details: dict[str, Any] = {}
        if comment is not None:
            details["comment"] = comment
        if reason is not None:
            details["reason"] = reason
        await self._repository.record_audit_entry(
            workspace_id=workspace_id,
            approval_request_id=request_id,
            actor_user_id=actor_user_id,
            action=audit_action,
            details=details or None,
        )
        await self._repository.record_outbox_event(
            workspace_id=workspace_id,
            approval_request_id=request_id,
            event_type=event_type.value,
            payload={
                "id": request_id,
                "workspace_id": workspace_id,
                "status": updated.status.value,
                "decided_by_user_id": actor_user_id,
            },
        )
        logger.info(
            "approval request %s",
            audit_action.value,
            extra={
                "context": {
                    "workspace_id": workspace_id,
                    "request_id": request_id,
                    "actor_user_id": actor_user_id,
                    "new_status": updated.status.value,
                }
            },
        )
        return updated
