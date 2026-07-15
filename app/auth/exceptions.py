class AuthError(Exception):
    """Base class for stub-auth failures."""


class InvalidCredentialsError(AuthError):
    """Missing, malformed, or unparsable Authorization header."""
