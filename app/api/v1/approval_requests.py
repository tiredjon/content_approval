from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_approval_service
from app.auth.dependencies import require_cancel, require_create, require_decide, require_read
from app.auth.models import Principal
from app.domain.enums import ApprovalStatus
from app.domain.exceptions import (
    ApprovalRequestNotFoundError,
    InvalidTransitionError,
    NotAuthorizedForDecisionError,
)
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


@contextmanager
def _map_domain_errors() -> Iterator[None]:
    """Translate domain exceptions to HTTP responses at every route below.

    Deliberately inline HTTPException mapping rather than a global exception handler —
    Phase 5 unifies *all* error responses (including auth's) into one RFC 7807 format,
    so building that machinery here now would just be redone shortly.
    """
    try:
        yield
    except ApprovalRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Approval request not found") from exc
    except NotAuthorizedForDecisionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("", response_model=ApprovalRequestOut, status_code=status.HTTP_201_CREATED)
async def create_approval_request(
    workspace_id: str,
    payload: ApprovalRequestCreate,
    principal: Principal = Depends(require_create),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    request = await service.create_request(
        workspace_id=workspace_id,
        created_by_user_id=principal.user_id,
        source_type=payload.source_type,
        source_id=payload.source_id,
        title=payload.title,
        description=payload.description,
        reviewer_user_ids=payload.reviewer_user_ids,
    )
    return ApprovalRequestOut.model_validate(request)


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
    with _map_domain_errors():
        request = await service.get_request(workspace_id=workspace_id, request_id=request_id)
    return ApprovalRequestOut.model_validate(request)


@router.post("/{request_id}/approve", response_model=ApprovalRequestOut)
async def approve_approval_request(
    workspace_id: str,
    request_id: str,
    payload: ApproveRequest,
    principal: Principal = Depends(require_decide),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    with _map_domain_errors():
        request = await service.approve_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            comment=payload.comment,
        )
    return ApprovalRequestOut.model_validate(request)


@router.post("/{request_id}/reject", response_model=ApprovalRequestOut)
async def reject_approval_request(
    workspace_id: str,
    request_id: str,
    payload: RejectRequest,
    principal: Principal = Depends(require_decide),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    with _map_domain_errors():
        request = await service.reject_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            reason=payload.reason,
        )
    return ApprovalRequestOut.model_validate(request)


@router.post("/{request_id}/cancel", response_model=ApprovalRequestOut)
async def cancel_approval_request(
    workspace_id: str,
    request_id: str,
    payload: CancelRequest,
    principal: Principal = Depends(require_cancel),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    with _map_domain_errors():
        request = await service.cancel_request(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_user_id=principal.user_id,
            reason=payload.reason,
        )
    return ApprovalRequestOut.model_validate(request)
