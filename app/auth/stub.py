import base64
import json
from collections.abc import Iterable

from pydantic import ValidationError

from app.auth.exceptions import InvalidCredentialsError
from app.auth.models import Action, Principal
from app.auth.provider import AuthProvider


class StubAuthProvider(AuthProvider):
    """Decodes `Authorization: Bearer <base64url(json)>` into a Principal.

    This is not real authentication — the token carries no signature, so anyone can
    mint one — but it lets the service be exercised locally/in tests without a real
    identity provider, behind the same `AuthProvider` interface a real one would use.
    """

    def authenticate(self, authorization_header: str | None) -> Principal:
        if authorization_header is None:
            raise InvalidCredentialsError("Missing Authorization header")

        scheme, _, token = authorization_header.partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise InvalidCredentialsError("Authorization header must be 'Bearer <token>'")

        try:
            data = json.loads(_b64url_decode(token))
        except ValueError as exc:
            raise InvalidCredentialsError("Malformed bearer token") from exc

        try:
            return Principal.model_validate(data)
        except ValidationError as exc:
            raise InvalidCredentialsError("Malformed bearer token") from exc


def _b64url_decode(token: str) -> bytes:
    padded = token + "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode_bearer_token(
    *, workspace_id: str, user_id: str, actions: Iterable[Action | str] = ()
) -> str:
    """Build a full `Authorization` header value — for local/manual testing only."""
    payload = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "actions": [Action(a).value for a in actions],
    }
    raw = json.dumps(payload).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"Bearer {token}"
