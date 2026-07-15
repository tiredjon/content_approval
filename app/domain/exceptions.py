from app.domain.enums import ApprovalStatus


class DomainError(Exception):
    """Base class for business-rule violations raised by the domain layer."""


class ApprovalRequestNotFoundError(DomainError):
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"Approval request not found: {request_id}")


class InvalidTransitionError(DomainError):
    """Raised when a decision is attempted on a request that already reached a final
    state — constraint: a request must never move from one final state to another."""

    def __init__(self, request_id: str, current_status: ApprovalStatus) -> None:
        self.request_id = request_id
        self.current_status = current_status
        super().__init__(
            f"Approval request {request_id} is already {current_status.value} and cannot be changed"
        )


class NotAuthorizedForDecisionError(DomainError):
    """Actor holds the coarse action (approval:decide/cancel) but isn't allowed to
    decide on this *specific* request — e.g. not a listed reviewer, or not its creator."""

    def __init__(self, request_id: str, actor_user_id: str, reason: str) -> None:
        self.request_id = request_id
        self.actor_user_id = actor_user_id
        super().__init__(f"{reason} (request {request_id}, user {actor_user_id})")
