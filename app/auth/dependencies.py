from fastapi import Depends, Header, HTTPException

from app.auth.exceptions import InvalidCredentialsError
from app.auth.models import Action, Principal
from app.auth.provider import AuthProvider
from app.auth.stub import StubAuthProvider

_default_provider = StubAuthProvider()


def get_auth_provider() -> AuthProvider:
    return _default_provider


async def get_principal(
    authorization: str | None = Header(default=None),
    provider: AuthProvider = Depends(get_auth_provider),
) -> Principal:
    try:
        return provider.authenticate(authorization)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_action(action: Action):
    """Dependency factory: resolves the Principal, then enforces that it belongs to
    the workspace named in the URL path and holds the given action.

    Relies on FastAPI resolving the `workspace_id` parameter below from the path
    operation's own `{workspace_id}` path parameter, so every route using this must
    declare that path segment.
    """

    async def _dependency(
        workspace_id: str,
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        if principal.workspace_id != workspace_id:
            raise HTTPException(status_code=403, detail="Not authorized for this workspace")
        if not principal.has_action(action):
            raise HTTPException(status_code=403, detail=f"Missing required action: {action.value}")
        return principal

    return _dependency


# Pre-built singletons for the fixed action set, so routes write `Depends(require_read)`
# instead of `Depends(require_action(Action.READ))` (the latter is a function call in an
# argument default, which is both a lint smell and re-builds the closure on every request).
require_read = require_action(Action.READ)
require_create = require_action(Action.CREATE)
require_decide = require_action(Action.DECIDE)
require_cancel = require_action(Action.CANCEL)
