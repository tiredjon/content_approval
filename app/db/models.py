from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import ApprovalStatus, AuditAction, SourceType

# Postgres gets native JSONB (indexable); every other dialect (SQLite, in tests) falls
# back to plain JSON. Values stored here must already be redacted of anything sensitive
# by the caller — see the "no secrets in events/logs" constraint in CLAUDE.md.
JSONVariant = JSON().with_variant(postgresql.JSONB(), "postgresql")


def _enum_values(enum_cls: type[PyEnum]) -> list[str]:
    """SQLAlchemy's Enum type stores `.name` by default; we want the lowercase `.value`."""
    return [member.value for member in enum_cls]


def _utcnow() -> datetime:
    """Python-side (not server_default) so every dialect gets microsecond precision —
    SQLite's CURRENT_TIMESTAMP only has second resolution, which made rows created in
    the same test collide and broke "list, newest first" ordering."""
    return datetime.now(UTC)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        # Lets child tables declare a composite FK on (workspace_id, id), which makes it
        # impossible at the schema level for an audit/outbox/reviewer row to point at an
        # approval request belonging to a different workspace.
        UniqueConstraint("workspace_id", "id", name="uq_approval_requests_workspace_id_id"),
        Index("ix_approval_requests_workspace_created", "workspace_id", "created_at"),
        Index("ix_approval_requests_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        SqlEnum(
            SourceType,
            name="source_type",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        SqlEnum(
            ApprovalStatus,
            name="approval_status",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    created_by_user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    decided_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewers: Mapped[list[ApprovalRequestReviewer]] = relationship(
        back_populates="approval_request", cascade="all, delete-orphan"
    )


class ApprovalRequestReviewer(Base):
    __tablename__ = "approval_request_reviewers"
    __table_args__ = (
        PrimaryKeyConstraint("workspace_id", "approval_request_id", "reviewer_user_id"),
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            ondelete="CASCADE",
            name="fk_approval_request_reviewers_approval_request",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String)
    approval_request_id: Mapped[str] = mapped_column(String)
    reviewer_user_id: Mapped[str] = mapped_column(String)

    approval_request: Mapped[ApprovalRequest] = relationship(back_populates="reviewers")


class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            ondelete="CASCADE",
            name="fk_audit_log_entries_approval_request",
        ),
        Index(
            "ix_audit_log_entries_workspace_request",
            "workspace_id",
            "approval_request_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    approval_request_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        SqlEnum(
            AuditAction,
            name="audit_action",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    # Structured, already-redacted details only (e.g. {"comment": "..."} / {"reason": "..."}).
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            ondelete="CASCADE",
            name="fk_outbox_events_approval_request",
        ),
        Index("ix_outbox_events_unpublished", "published_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    approval_request_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # Payload must only ever contain external ids + safe domain fields — never secrets,
    # tokens, emails, storage keys, signed URLs, provider URLs, or raw provider payloads.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        # Scoped per workspace + logical operation, so keys can never collide across
        # tenants or endpoints. `route` is an app-constructed logical key, e.g. "create"
        # or f"approve:{request_id}" — not the literal HTTP path.
        PrimaryKeyConstraint("workspace_id", "route", "idempotency_key"),
    )

    workspace_id: Mapped[str] = mapped_column(String)
    route: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String)
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
