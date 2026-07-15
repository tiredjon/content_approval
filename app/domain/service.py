from collections.abc import Sequence

from app.domain.entities import ApprovalRequest
from app.domain.enums import ApprovalStatus, AuditAction, SourceType
from app.domain.exceptions import ApprovalRequestNotFoundError
from app.domain.ids import generate_id
from app.domain.repository import ApprovalRequestRepository


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
            event_type="approval_request.created",
            payload={
                "id": request_id,
                "workspace_id": workspace_id,
                "source_type": source_type.value,
                "source_id": source_id,
                "status": request.status.value,
                "reviewer_user_ids": list(reviewer_user_ids),
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
