from abc import ABC, abstractmethod

from app.auth.models import Principal


class AuthProvider(ABC):
    """Resolves an `Authorization` header into a Principal.

    Routes depend on this interface, not on a concrete implementation, so the stub
    below can be replaced with real verification (JWT, session lookup, ...) later
    without touching route code.
    """

    @abstractmethod
    def authenticate(self, authorization_header: str | None) -> Principal:
        raise NotImplementedError
