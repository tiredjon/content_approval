import uuid
from collections.abc import Iterable
from typing import Any

from fastapi.testclient import TestClient

from app.auth.models import Action
from app.auth.stub import encode_bearer_token

DEFAULT_APPROVAL_REQUEST_PAYLOAD = {
    "sourceType": "publication",
    "sourceId": "pub_123",
    "title": "Instagram reel draft",
    "description": "Needs final approval",
    "reviewerUserIds": ["usr_1", "usr_2"],
}


def auth_headers(
    *, workspace_id: str, user_id: str = "usr_1", actions: Iterable[Action | str] = ()
) -> dict[str, str]:
    return {
        "Authorization": encode_bearer_token(
            workspace_id=workspace_id, user_id=user_id, actions=actions
        )
    }


def create_approval_request(
    client: TestClient,
    *,
    workspace_id: str = "ws_1",
    created_by_user_id: str = "usr_1",
    idempotency_key: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload = {**DEFAULT_APPROVAL_REQUEST_PAYLOAD, **overrides}
    headers = auth_headers(
        workspace_id=workspace_id, user_id=created_by_user_id, actions=["approval:create"]
    )
    # A fresh key per call by default, so tests that don't care about idempotency
    # (the vast majority) never accidentally collide with each other's keys.
    headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/approval-requests",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
