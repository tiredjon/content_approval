class DomainError(Exception):
    """Base class for business-rule violations raised by the domain layer."""


class ApprovalRequestNotFoundError(DomainError):
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"Approval request not found: {request_id}")
