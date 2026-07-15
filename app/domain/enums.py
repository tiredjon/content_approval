from enum import StrEnum


class SourceType(StrEnum):
    PUBLICATION = "publication"
    SCENARIO = "scenario"
    EDIT = "edit"
    EXTERNAL = "external"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def is_final(self) -> bool:
        return self is not ApprovalStatus.PENDING


class AuditAction(StrEnum):
    CREATED = "created"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OutboxEventType(StrEnum):
    APPROVAL_REQUEST_CREATED = "approval_request.created"
    APPROVAL_REQUEST_APPROVED = "approval_request.approved"
    APPROVAL_REQUEST_REJECTED = "approval_request.rejected"
    APPROVAL_REQUEST_CANCELLED = "approval_request.cancelled"
