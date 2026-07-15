from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import ApprovalStatus, SourceType


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Storage-agnostic representation of an approval request.

    The repository adapter (app/db/repository.py) is responsible for translating
    between this and the ORM row; the service/API layers only ever see this type.
    """

    id: str
    workspace_id: str
    source_type: SourceType
    source_id: str
    title: str
    description: str | None
    reviewer_user_ids: tuple[str, ...]
    status: ApprovalStatus
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime
    decided_by_user_id: str | None
    decided_at: datetime | None
    decision_comment: str | None
    decision_reason: str | None
