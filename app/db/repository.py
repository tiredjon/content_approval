from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ApprovalRequest as ApprovalRequestRow
from app.db.models import ApprovalRequestReviewer, AuditLogEntry, OutboxEvent
from app.domain.entities import ApprovalRequest
from app.domain.enums import ApprovalStatus, AuditAction, SourceType
from app.domain.ids import generate_id
from app.domain.repository import ApprovalRequestRepository


class SqlApprovalRequestRepository(ApprovalRequestRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> ApprovalRequest:
        row = ApprovalRequestRow(
            id=request_id,
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id,
            title=title,
            description=description,
            status=ApprovalStatus.PENDING,
            created_by_user_id=created_by_user_id,
            reviewers=[
                ApprovalRequestReviewer(
                    workspace_id=workspace_id,
                    approval_request_id=request_id,
                    reviewer_user_id=reviewer_id,
                )
                for reviewer_id in reviewer_user_ids
            ],
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row, reviewer_user_ids)

    async def get(self, *, workspace_id: str, request_id: str) -> ApprovalRequest | None:
        stmt = (
            select(ApprovalRequestRow)
            .options(selectinload(ApprovalRequestRow.reviewers))
            .where(
                ApprovalRequestRow.workspace_id == workspace_id,
                ApprovalRequestRow.id == request_id,
            )
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_entity(row, [r.reviewer_user_id for r in row.reviewers])

    async def list(
        self,
        *,
        workspace_id: str,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ApprovalRequest], int]:
        conditions = [ApprovalRequestRow.workspace_id == workspace_id]
        if status is not None:
            conditions.append(ApprovalRequestRow.status == status)

        total = (
            await self._session.execute(
                select(func.count()).select_from(ApprovalRequestRow).where(*conditions)
            )
        ).scalar_one()

        stmt = (
            select(ApprovalRequestRow)
            .options(selectinload(ApprovalRequestRow.reviewers))
            .where(*conditions)
            .order_by(ApprovalRequestRow.created_at.desc(), ApprovalRequestRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        entities = [_to_entity(row, [r.reviewer_user_id for r in row.reviewers]) for row in rows]
        return entities, total

    async def record_audit_entry(
        self,
        *,
        workspace_id: str,
        approval_request_id: str,
        actor_user_id: str,
        action: AuditAction,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditLogEntry(
                id=generate_id("aud"),
                workspace_id=workspace_id,
                approval_request_id=approval_request_id,
                actor_user_id=actor_user_id,
                action=action,
                details=details,
            )
        )

    async def record_outbox_event(
        self,
        *,
        workspace_id: str,
        approval_request_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self._session.add(
            OutboxEvent(
                id=generate_id("evt"),
                workspace_id=workspace_id,
                approval_request_id=approval_request_id,
                event_type=event_type,
                payload=payload,
            )
        )


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip (Postgres doesn't), so a freshly-created row
    and the same row re-fetched from the DB would otherwise serialize differently.
    Every datetime we write is already UTC wall-clock time, so a naive value just needs
    the label attached; an aware one (Postgres) is normalized to UTC for consistency."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _to_entity(row: ApprovalRequestRow, reviewer_user_ids: Sequence[str]) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        workspace_id=row.workspace_id,
        source_type=row.source_type,
        source_id=row.source_id,
        title=row.title,
        description=row.description,
        reviewer_user_ids=tuple(reviewer_user_ids),
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        decided_by_user_id=row.decided_by_user_id,
        decided_at=_as_utc(row.decided_at),
        decision_comment=row.decision_comment,
        decision_reason=row.decision_reason,
    )
