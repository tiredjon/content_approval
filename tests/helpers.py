from collections.abc import Iterable

from app.auth.models import Action
from app.auth.stub import encode_bearer_token


def auth_headers(
    *, workspace_id: str, user_id: str = "usr_1", actions: Iterable[Action | str] = ()
) -> dict[str, str]:
    return {
        "Authorization": encode_bearer_token(
            workspace_id=workspace_id, user_id=user_id, actions=actions
        )
    }
