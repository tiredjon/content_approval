from datetime import datetime

from pydantic import Field, field_validator

from app.domain.enums import ApprovalStatus, SourceType
from app.schemas.common import CamelModel


class ApprovalRequestCreate(CamelModel):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10_000)
    reviewer_user_ids: list[str] = Field(default_factory=list)

    @field_validator("source_id", "title")
    @classmethod
    def _strip_and_require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("reviewer_user_ids")
    @classmethod
    def _validate_reviewer_ids(cls, value: list[str]) -> list[str]:
        stripped = [v.strip() for v in value]
        if any(not v for v in stripped):
            raise ValueError("reviewer_user_ids must not contain blank ids")
        if len(stripped) != len(set(stripped)):
            raise ValueError("reviewer_user_ids must not contain duplicate ids")
        return stripped


class ApprovalRequestOut(CamelModel):
    id: str
    workspace_id: str
    source_type: SourceType
    source_id: str
    title: str
    description: str | None
    reviewer_user_ids: list[str]
    status: ApprovalStatus
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    decided_by_user_id: str | None
    decided_at: datetime | None
    decision_comment: str | None
    decision_reason: str | None


class ApprovalRequestListOut(CamelModel):
    items: list[ApprovalRequestOut]
    total: int
    limit: int
    offset: int
