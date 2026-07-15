from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_approval_service
from app.api.idempotency import run_idempotent
from app.auth.dependencies import require_cancel, require_create, require_decide, require_read
from app.auth.models import Principal
from app.db.session import get_db
from app.domain.enums import ApprovalStatus
from app.domain.service import ApprovalService
from app.schemas.approval_request import (
    ApprovalRequestCreate,
    ApprovalRequestListOut,
    ApprovalRequestOut,
    ApproveRequest,
    CancelRequest,
    RejectRequest,
)

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/approval-requests",
    tags=["approval-requests"],
)

# Required for create (a retry without one would create a second resource — exactly
# the duplicate the assignment says must never happen). Optional for decisions: the
# conditional-UPDATE state machine already makes those safe to retry without one
# (a bare retry just gets 409 "already decided"); the key upgrades that into a clean
# replay of the original response, but isn't load-bearing for correctness there.
_REQUIRED_IDEMPOTENCY_KEY = Header(alias="Idempotency-Key", min_length=1, max_length=255)
_OPTIONAL_IDEMPOTENCY_KEY = Header(
    default=None, alias="Idempotency-Key", min_length=1, max_length=255
)


@router.post("", response_model=ApprovalRequestOut, status_code=status.HTTP_201_CREATED)
async def create_approval_request(
    workspace_id: str,
    payload: ApprovalRequestCreate,
    response: Response,
    idempotency_key: str = _REQUIRED_IDEMPOTENCY_KEY,
    principal: Principal = Depends(require_create),
    service: ApprovalService = Depends(get_approval_service),
    session: AsyncSession = Depends(get_db),
) -> ApprovalRequestOut:
    async def _handler() -> tuple[ApprovalRequestOut, int]:
        request = await service.create_request(
            workspace_id=workspace_id,
            created_by_user_id=principal.user_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            title=payload.title,
            description=payload.description,
            reviewer_user_ids=payload.reviewer_user_ids,
        )
        return ApprovalRequestOut.model_validate(request), status.HTTP_201_CREATED

    result, status_code = await run_idempotent(
        session=session,
        workspace_id=workspace_id,
        route="create",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
        handler=_handler,
        response_model=ApprovalRequestOut,
    )
    response.status_code = status_code
    return result


@router.get("", response_model=ApprovalRequestListOut)
async def list_approval_requests(
    workspace_id: str,
    status_filter: ApprovalStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_read),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestListOut:
    items, total = await service.list_requests(
        workspace_id=workspace_id, status=status_filter, limit=limit, offset=offset
    )
    return ApprovalRequestListOut(
        items=[ApprovalRequestOut.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{request_id}", response_model=ApprovalRequestOut)
async def get_approval_request(
    workspace_id: str,
    request_id: str,
    principal: Principal = Depends(require_read),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    request = await service.get_request(workspace_id=workspace_id, request_id=request_id)
    return ApprovalRequestOut.model_validate(request)


@router.post("/{request_id}/approve", response_model=ApprovalRequestOut)
async def approve_approval_request(
    workspace_id: str,
    request_id: str,
    payload: ApproveRequest,
    response: Response,
    idempotency_key: str | None = _OPTIONAL_IDEMPOTENCY_KEY,
    principal: Principal = Depends(require_decide),
    service: ApprovalService = Depends(get_approval_service),
    session: AsyncSession = Depends(get_db),
) -> ApprovalRequestOut:
    async def _handler() -> tuple[ApprovalRequestOut, int]:
        request = await service.approve_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            comment=payload.comment,
        )
        return ApprovalRequestOut.model_validate(request), status.HTTP_200_OK

    result, status_code = await run_idempotent(
        session=session,
        workspace_id=workspace_id,
        route=f"approve:{request_id}",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
        handler=_handler,
        response_model=ApprovalRequestOut,
    )
    response.status_code = status_code
    return result


@router.post("/{request_id}/reject", response_model=ApprovalRequestOut)
async def reject_approval_request(
    workspace_id: str,
    request_id: str,
    payload: RejectRequest,
    response: Response,
    idempotency_key: str | None = _OPTIONAL_IDEMPOTENCY_KEY,
    principal: Principal = Depends(require_decide),
    service: ApprovalService = Depends(get_approval_service),
    session: AsyncSession = Depends(get_db),
) -> ApprovalRequestOut:
    async def _handler() -> tuple[ApprovalRequestOut, int]:
        request = await service.reject_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            reason=payload.reason,
        )
        return ApprovalRequestOut.model_validate(request), status.HTTP_200_OK

    result, status_code = await run_idempotent(
        session=session,
        workspace_id=workspace_id,
        route=f"reject:{request_id}",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
        handler=_handler,
        response_model=ApprovalRequestOut,
    )
    response.status_code = status_code
    return result


@router.post("/{request_id}/cancel", response_model=ApprovalRequestOut)
async def cancel_approval_request(
    workspace_id: str,
    request_id: str,
    payload: CancelRequest,
    response: Response,
    idempotency_key: str | None = _OPTIONAL_IDEMPOTENCY_KEY,
    principal: Principal = Depends(require_cancel),
    service: ApprovalService = Depends(get_approval_service),
    session: AsyncSession = Depends(get_db),
) -> ApprovalRequestOut:
    async def _handler() -> tuple[ApprovalRequestOut, int]:
        request = await service.cancel_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            reason=payload.reason,
        )
        return ApprovalRequestOut.model_validate(request), status.HTTP_200_OK

    result, status_code = await run_idempotent(
        session=session,
        workspace_id=workspace_id,
        route=f"cancel:{request_id}",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
        handler=_handler,
        response_model=ApprovalRequestOut,
    )
    response.status_code = status_code
    return result
