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
